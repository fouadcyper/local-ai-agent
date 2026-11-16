#!/usr/bin/env python3
"""Thread-safe bridge between the Python agent and the official MCP client SDK."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time


HERE = os.path.dirname(os.path.abspath(__file__))
NODE = os.environ.get("LOCAL_AI_AGENT_NODE", "node")


class MCPManager:
    def __init__(self):
        self.process = None
        self.lock = threading.RLock()
        self.pending = {}
        self.counter = 0
        self.reader = None
        self.tools = {}
        self.servers = {}
        self.errors = {}

    def _ensure_bridge(self):
        if self.process and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            [NODE, os.path.join(HERE, "mcp_bridge.mjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, cwd=HERE,
        )
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name="mcp-bridge-reader")
        self.reader.start()

    def _read_loop(self):
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            try:
                response = json.loads(line)
                waiter = self.pending.get(response.get("id"))
                if waiter:
                    waiter["response"] = response
                    waiter["event"].set()
            except Exception:
                continue
        for waiter in list(self.pending.values()):
            waiter["response"] = {"error": "MCP bridge stopped"}
            waiter["event"].set()

    def _request(self, op, timeout=45, **payload):
        with self.lock:
            self._ensure_bridge()
            self.counter += 1
            request_id = self.counter
            waiter = {"event": threading.Event(), "response": None}
            self.pending[request_id] = waiter
            body = {"id": request_id, "op": op, **payload}
            try:
                self.process.stdin.write(json.dumps(body, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except Exception as exc:
                self.pending.pop(request_id, None)
                raise RuntimeError(f"MCP bridge write failed: {exc}")
        if not waiter["event"].wait(timeout):
            self.pending.pop(request_id, None)
            raise TimeoutError(f"MCP operation timed out after {timeout}s")
        response = self.pending.pop(request_id, {}).get("response") or {}
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response.get("result")

    @staticmethod
    def _safe_name(value):
        value = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value)).strip("_")
        return value[:48] or "tool"

    def connect(self, server):
        server = dict(server or {})
        server_id = self._safe_name(server.get("id") or server.get("name") or "server")
        server["id"] = server_id
        result = self._request("connect", timeout=90, server=server)
        self.servers[server_id] = {**server, "connected": True, "serverInfo": result.get("serverInfo")}
        self.errors.pop(server_id, None)
        self._register_tools(server_id, result.get("tools") or [])
        return self.server_public(server_id)

    def _register_tools(self, server_id, tools):
        for key in [k for k, item in self.tools.items() if item["server_id"] == server_id]:
            self.tools.pop(key, None)
        for tool in tools:
            original = tool.get("name") or "tool"
            name = f"mcp__{self._safe_name(server_id)}__{self._safe_name(original)}"
            annotations = tool.get("annotations") or {}
            self.tools[name] = {
                "server_id": server_id,
                "original_name": original,
                "description": tool.get("description") or f"MCP tool {original}",
                "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
                "annotations": annotations,
            }

    def disconnect(self, server_id):
        server_id = self._safe_name(server_id)
        try:
            self._request("disconnect", server_id=server_id, timeout=20)
        finally:
            self.servers.pop(server_id, None)
            for key in [k for k, item in self.tools.items() if item["server_id"] == server_id]:
                self.tools.pop(key, None)
        return {"id": server_id, "connected": False}

    def sync(self, configured):
        configured = [item for item in (configured or []) if item.get("enabled")]
        wanted = {self._safe_name(item.get("id") or item.get("name")): item for item in configured}
        for server_id in list(self.servers):
            if server_id not in wanted:
                self.disconnect(server_id)
        results = []
        for server_id, server in wanted.items():
            if server_id in self.servers:
                results.append(self.server_public(server_id))
                continue
            try:
                results.append(self.connect(server))
            except Exception as exc:
                self.errors[server_id] = str(exc)
                results.append({"id": server_id, "name": server.get("name", server_id),
                                "connected": False, "error": str(exc)})
        return results

    def tool_definitions(self):
        definitions = []
        for name, item in self.tools.items():
            definitions.append({"type": "function", "function": {
                "name": name,
                "description": item["description"],
                "parameters": item["inputSchema"],
            }})
        return definitions

    def is_mcp_tool(self, name):
        return name in self.tools

    def annotations(self, name):
        return (self.tools.get(name) or {}).get("annotations") or {}

    def call(self, name, arguments):
        item = self.tools.get(name)
        if not item:
            raise KeyError(f"Unknown MCP tool: {name}")
        result = self._request("call", timeout=180, server_id=item["server_id"],
                               tool=item["original_name"], arguments=arguments or {})
        return result

    def server_public(self, server_id):
        server = self.servers.get(server_id, {})
        tool_count = sum(1 for item in self.tools.values() if item["server_id"] == server_id)
        return {"id": server_id, "name": server.get("name", server_id), "connected": True,
                "transport": server.get("transport", "stdio"),
                "tools": tool_count, "tool_count": tool_count,
                "serverInfo": server.get("serverInfo")}

    def status(self):
        items = [self.server_public(server_id) for server_id in self.servers]
        for server_id, error in self.errors.items():
            if server_id not in self.servers:
                items.append({"id": server_id, "connected": False, "error": error})
        return {"servers": items, "connected": sum(1 for item in items if item.get("connected")),
                "tool_count": len(self.tools),
                "bridge_running": bool(self.process and self.process.poll() is None)}


MCP_MANAGER = MCPManager()
