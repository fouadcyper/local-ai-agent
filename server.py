#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local AI Agent — واجهة GUI لوكيل AI محلي مبني على Ollama.
- شبيه بـ OpenCode: محادثة + أدوات تتحكم بالجهاز (bash, قراءة/كتابة ملفات)
- كل أمر خطير يحتاج موافقتك قبل التنفيذ (مثل زر الموافقة في OpenCode)
- Python stdlib للواجهة الخلفية + SDK الرسمي لـMCP عبر Node.js
"""
import base64
import html
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_settings import (discover_models, load_settings, public_settings,
                            save_settings)
from mcp_manager import MCP_MANAGER

OLLAMA = os.environ.get("OLLAMA_HOST", load_settings().get("ollama_url", "http://127.0.0.1:11434"))
PORT = int(os.environ.get("LOCALAGENT_PORT", "7777"))
HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_STEPS = 12          # أقصى عدد جولات أدوات لكل رسالة
MAX_OUTPUT = 24000      # أقصى حجم مخرجات تُغذّى للنموذج
CMD_TIMEOUT = 90        # ثانية

MCP_PRESETS = [
    {"id": "filesystem", "name": "Filesystem (official, read-only by default)", "transport": "stdio",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@2.3.0", "--read-only", HOME],
     "enabled": False, "description": "Scoped local file access. Remove --read-only only if you want write tools."},
    {"id": "memory", "name": "Memory (official)", "transport": "stdio",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory@2.1.1"],
     "enabled": False, "description": "Persistent knowledge graph memory."},
    {"id": "sequential-thinking", "name": "Sequential Thinking (official)", "transport": "stdio",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking@2.1.1"],
     "enabled": False, "description": "Structured multi-step reasoning tool."},
    {"id": "fetch", "name": "Fetch (official)", "transport": "stdio",
     "command": "uvx", "args": ["mcp-server-fetch==2026.11.11"],
     "enabled": False, "description": "Fetch public web pages with robots and private-address guards."},
    {"id": "git", "name": "Git (official)", "transport": "stdio",
     "command": "uvx", "args": ["mcp-server-git==2026.11.11", "--repository", HOME],
     "enabled": False, "description": "Inspect and operate a selected Git repository."},
    {"id": "github", "name": "GitHub MCP Server (official OAuth)", "transport": "stdio",
     "command": "docker", "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server", "stdio"],
     "enabled": False, "description": "Official GitHub tools. The first connection starts GitHub OAuth/device login."},
    {"id": "brave-search", "name": "Brave Search", "transport": "stdio",
     "command": "npx", "args": ["-y", "@brave/brave-search-mcp-server"], "env": {"BRAVE_API_KEY": ""},
     "enabled": False, "description": "Web and local search. Add BRAVE_API_KEY before connecting."},
]

SYSTEM_PROMPT_CC = f"""You are "Local AI Agent", a local CLI coding agent running on the user's Linux laptop — in the spirit of Claude Code, but 100% on-device (user=fouad, home={HOME}).

<Executing actions with care>
Consider the reversibility and blast radius of every action. Local, reversible actions (reading, listing, writing your own scratch files, running tests) are fine to do freely. Actions that are destructive, hard to reverse, or outward-facing (deleting, force-push, sending anything anywhere, shared-state changes) require the user's explicit confirmation — the UI gates missing approval, not you; don't route around it. A single approval never extends to a later, different action. If you find unexpected state (unfamiliar files, config, locks), investigate before deleting or overwriting; when unsure, prefer a reversible step over an irreversible one. If a tool call is denied, do not re-attempt the identical call; reconsider why.

<Exploratory questions>
For open-ended questions ("what could we do about X?", "how should we approach this?"), answer in 2-3 sentences with a recommendation and the main tradeoff. Do not implement until the user agrees.

<Finish the request>
Large tasks can and should be finished in the turn that asked for them. Ask first only when the request genuinely can't be disambiguated from its words, or a needed fact/access exists only with the user. Approval of one choice is never approval of all others. When one part is blocked or unclear, finish everything else and open your report with what's stuck.

<Doing the work>
The user primarily wants software-engineering work: bugs, features, refactors, explanations. Interpret generic instructions in that context (change the code, don't just name the method). Match the requested scope — no unrelated extras, no speculative rewrites, no compatibility shims for platforms that don't exist here.

<Reporting outcomes>
Report what actually happened, not what you intended. Every claim ("done", "fixed", "verified") must rest on tool output you saw this session; if you didn't check, say so. If anything failed or was skipped, say it in the first sentence, even if the rest worked. Never hide a failure inside a success summary.

<Communication>
Lead with the outcome. Plain prose, complete sentences, no arrow-chain shorthand. Simple question → direct answer; complex task → still lead with the result, then the detail. One or two sentence end-of-turn summary. Keep code comments rare and only for constraints the code can't show.

<Format>
Reply in the same language the user writes in (default: Arabic). NEVER output <think> blocks. Do not ask permission to be helpful; act, then report honestly.
"""

SYSTEM_PROMPT = f"""You are "Local AI Agent", an AI agent whose model runs locally on the user's Linux computer.
Environment: user={os.environ.get("USER", "local-user")}, home={HOME}. The model conversation stays on-device. Explicitly enabled connectors such as web search, GitHub, Telegram, and remote MCP servers send only the requests needed for those tools.

You can use tools to actually control the machine:
- run_bash(command, timeout): run a bash command and get stdout+stderr. Working directory is tracked per session.
- read_file(path): read a text file.
- write_file(path, content): create/overwrite a file.
- list_directory(path): list files in a directory.
- find_files(pattern, path, limit): find files by name pattern (e.g. '*.pdf').
- search_content(pattern, path): search inside files recursively (like grep -ri).
- find_files(pattern, path, limit): find files by name pattern (e.g. '*.pdf').
- search_content(pattern, path): search inside files recursively (like grep -ri).

Rules:
- Reply in the same language the user writes in (default: Arabic).
- Be concise and practical. Use tools when the user asks for something real; don't use them for chit-chat.
- Only ONE tool call per reply when possible. After a tool result comes back, decide the next step.
- Never claim something ran or succeeded until you see the tool result.
- Destructive commands are gated by the UI (user must approve them) — that's normal, not an error.
- If a command fails, read the error and try to fix it; give up after a few attempts and explain.
- NEVER output <think> blocks. Answer (or call a tool) directly.
"""

PROMPTS = {"local": SYSTEM_PROMPT, "claudecode": SYSTEM_PROMPT_CC}

_PROFILE_CACHE = {"text": None, "ts": 0.0}

def collect_system_profile():
    """صورة سريعة (read-only) عن الجهاز — بتتنحفظ 6 ساعات وما بتحتاج صلاحية."""
    now = time.time()
    if _PROFILE_CACHE["text"] and (now - _PROFILE_CACHE["ts"]) < 6 * 3600:
        return _PROFILE_CACHE["text"]

    def run(cmd):
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
            return (out.stdout or "").strip()
        except Exception:
            return ""

    os_name = run(". /etc/os-release && echo $PRETTY_NAME") or run("uname -o")
    kernel = run("uname -r")
    arch = run("uname -m")
    cpu_model = run("lscpu | grep -E '^Model name' | cut -d: -f2- | sed 's/^ *//'")
    cores = run("nproc")
    cpu = f"{cpu_model} — {cores} خيوط"
    mem = run("free -h | awk '/^Mem:/ {print $2\" total, \"$3\" used, \"$4\" free\"}'")
    swap = run("free -h | awk '/^Swap:/ {print $2\" total\"}'")
    disk = run("df -h / | awk 'NR==2 {print $4\" متاح من \"$2\" (\"$5\" مستخدم)\"}'")
    gpu = run("lspci 2>/dev/null | grep -iE 'vga|3d|display' | cut -d: -f3- | sed 's/^ *//' | paste -sd' | '")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or run("echo $XDG_CURRENT_DESKTOP")
    shell = os.path.basename(os.environ.get("SHELL", "?"))
    tools = run("for t in apt flatpak snap pip3 npm node python3 htop git curl wget jq; do command -v $t >/dev/null && printf '%s ' $t; done")
    memline = mem + (f" | Swap: {swap}" if swap else "")
    lines = [
        f"- OS: {os_name} (نواة {kernel}, {arch})",
        f"- CPU: {cpu}",
        f"- RAM: {memline}",
        f"- القرص /: {disk}",
        f"- GPU: {gpu or 'غير معلوم'}",
        f"- سطح المكتب: {desktop or '?'} | الشل: {shell} | المستخدم: {run('id -un') or '?'}",
        f"- أدوات مثبتة: {tools}",
    ]
    text = ("هذه صورة عن جهاز المستخدم (لقطة وقت بدء الجلسة — قد تتغير القيم؛ "
            "تحقق بأدوات القراءة عند الحاجة):\n" + "\n".join(lines))
    _PROFILE_CACHE["text"] = text
    _PROFILE_CACHE["ts"] = now
    return text

TOOLS = [
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "Execute a bash command on the local machine and return stdout/stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The bash command to run"},
            "timeout": {"type": "integer", "description": "Optional timeout in seconds"},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file and return its content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Absolute or relative path"},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a text file with the given content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Absolute or relative path"},
            "content": {"type": "string", "description": "Full file content"},
        }, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "find_files",
        "description": "Find files by name pattern (case-insensitive glob like '*.py' or 'report*'). Returns matching paths.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.pdf'"},
            "path": {"type": "string", "description": "Where to search (default: cwd)"},
            "limit": {"type": "integer", "description": "Max results (default 50, max 200)"},
        }, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "search_content",
        "description": "Search file contents recursively (like grep -ri). Returns matching lines with file and line number.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Text/regex to search for"},
            "path": {"type": "string", "description": "Where to search (default: cwd)"},
        }, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "list_directory",
        "description": "List files and folders in a directory.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory path (default: current working directory)"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the public web and return current result titles, URLs, and snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "1 to 10 results"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Fetch readable text from a public http/https URL. Private and local network addresses are blocked.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Public URL to fetch"},
            "max_chars": {"type": "integer", "description": "Maximum returned characters"},
        }, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "github_search",
        "description": "Search GitHub repositories, issues, pull requests, or code using the authenticated gh CLI.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "type": {"type": "string", "enum": ["repositories", "issues", "prs", "code"]},
            "limit": {"type": "integer"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "github_create_issue",
        "description": "Create an issue in a GitHub repository. This is an external write and always requires approval.",
        "parameters": {"type": "object", "properties": {
            "repo": {"type": "string", "description": "owner/repository"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        }, "required": ["repo", "title"]}}},
]

# أنماط ممنوعة دائماً حتى في وضع الموافقة التلقائية — بتضل تحتاج موافقة يدوية
HARD_BLOCK = [
    r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|/\*|~)(\s|$)",
    r"mkfs", r"dd\s+.*of=/dev", r">\s*/dev/sd", r":\(\)\s*\{",
    r"chmod\s+-R\s+777\s+/", r"shutdown", r"reboot", r"poweroff", r"halt",
]

def tool_summary(name, args):
    if name == "run_bash":
        return args.get("command", "")
    if name == "write_file":
        path = args.get("path", "?")
        return f"كتابة {path} ({len(args.get('content', ''))} حرف)"
    if name == "read_file":
        return args.get("path", "?")
    if MCP_MANAGER.is_mcp_tool(name):
        return f"{name}: " + json.dumps(args, ensure_ascii=False)
    if name == "github_create_issue":
        return f"Create GitHub issue in {args.get('repo', '?')}: {args.get('title', '')}"
    return args.get("path") or json.dumps(args, ensure_ascii=False)

def clip(s, n=MAX_OUTPUT):
    if s is None:
        return ""
    s = str(s)
    if len(s) > n:
        return s[:n] + f"\n…[مخرجات مقطوعة — الأصل {len(s)} حرف]"
    return s

def strip_think(text):
    if not text:
        return ""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return text.strip()

class Session:
    def __init__(self):
        self.id = str(uuid.uuid4())[:8]
        self.created = time.time()
        self.cwd = HOME
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.pending = None   # {"tool": {...}, "queue": [...]}
        self.lock = threading.Lock()
        self.title = "New conversation"
        self.prompt_key = "local"

SESSIONS = {}
SESSIONS_LOCK = threading.Lock()

def get_session(sid):
    with SESSIONS_LOCK:
        s = SESSIONS.get(sid)
        if s is None:
            s = Session()
            if sid:
                s.id = sid
            SESSIONS[s.id] = s
        return s

def session_public(s):
    public_messages = []
    for message in s.history:
        if message.get("role") == "system":
            continue
        copy = {key: value for key, value in message.items() if key != "images"}
        if message.get("images"):
            copy["attachments"] = [{"type": "image", "name": "Image attachment"} for _ in message["images"]]
        public_messages.append(copy)
    return {"id": s.id, "title": s.title, "created": s.created, "cwd": s.cwd,
            "messages": public_messages,
            "prompt": s.prompt_key}


def _decode_attachment(item):
    encoded = str(item.get("data") or "")
    if "," in encoded and encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 attachment: {exc}")


def _whisper_command(settings):
    configured = str(settings.get("whisper_command") or "auto")
    candidates = [] if configured == "auto" else [os.path.expanduser(configured)]
    candidates += [
        os.path.join(HOME, "whisper.cpp", "build", "bin", "whisper-cli"),
        os.path.join(HOME, "FOUAD-AI-RECOVERY", "ENGINES", "linux-x64", "whisper", "whisper-cli"),
        "whisper-cli",
    ]
    for candidate in candidates:
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
        found = subprocess.run(["sh", "-lc", f"command -v {shlex.quote(candidate)}"], capture_output=True, text=True).stdout.strip()
        if found:
            return found
    return ""


def transcribe_audio(raw, filename, mime_type):
    media = load_settings().get("media", {})
    command = _whisper_command(media)
    model = os.path.expanduser(str(media.get("whisper_model") or ""))
    if not command or not os.path.isfile(model):
        raise RuntimeError("Local Whisper is not configured. Set the command and model path in Settings → Media.")
    suffix = os.path.splitext(filename or "")[1].lower() or ".audio"
    with tempfile.TemporaryDirectory(prefix="local-ai-agent-audio-") as directory:
        source = os.path.join(directory, "input" + suffix)
        wav = os.path.join(directory, "audio.wav")
        with open(source, "wb") as handle:
            handle.write(raw)
        converted = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", source,
                                    "-ar", "16000", "-ac", "1", wav], capture_output=True, text=True, timeout=120)
        if converted.returncode:
            raise RuntimeError("Could not decode audio: " + converted.stderr[-500:])
        result = subprocess.run([command, "-m", model, "-f", wav, "-l", "auto", "-nt", "-np"],
                                capture_output=True, text=True, timeout=300)
        transcript = (result.stdout or "").strip()
        if result.returncode or not transcript:
            raise RuntimeError("Whisper transcription failed: " + (result.stderr or "no speech detected")[-700:])
        return transcript


def process_attachments(items):
    media = load_settings().get("media", {})
    max_bytes = max(1, min(int(media.get("max_upload_mb", 24)), 100)) * 1024 * 1024
    total = 0
    images, transcripts, public = [], [], []
    for item in (items or [])[:8]:
        raw = _decode_attachment(item)
        total += len(raw)
        if total > max_bytes:
            raise ValueError(f"Attachments exceed the {max_bytes // (1024 * 1024)} MB limit")
        mime_type = str(item.get("type") or "application/octet-stream").lower()
        name = os.path.basename(str(item.get("name") or "attachment"))
        if mime_type.startswith("image/"):
            images.append(base64.b64encode(raw).decode("ascii"))
            public.append({"type": "image", "name": name, "size": len(raw)})
        elif mime_type.startswith("audio/") or os.path.splitext(name)[1].lower() in (".mp3", ".wav", ".ogg", ".m4a", ".webm", ".flac"):
            transcript = transcribe_audio(raw, name, mime_type)
            transcripts.append(f"Voice message ({name}) transcript:\n{transcript}")
            public.append({"type": "audio", "name": name, "size": len(raw), "transcript": transcript})
        else:
            raise ValueError(f"Unsupported attachment type: {mime_type}")
    return images, transcripts, public


def choose_vision_model(requested):
    settings = load_settings()
    preferred = str(settings.get("media", {}).get("vision_model") or "auto")
    if any(marker in requested.lower() for marker in ("vision", "llava", "vl", "gemma3", "minicpm-v")):
        return requested
    try:
        with urllib.request.urlopen(str(settings.get("ollama_url") or OLLAMA).rstrip("/") + "/api/tags", timeout=5) as response:
            names = [item.get("name", "") for item in json.loads(response.read().decode("utf-8")).get("models", [])]
    except Exception:
        names = []
    if preferred != "auto" and preferred in names:
        return preferred
    vision = [name for name in names if any(marker in name.lower() for marker in ("vision", "llava", "vl", "gemma3", "minicpm-v"))]
    if not vision:
        raise RuntimeError("No vision model is installed in Ollama. Install one or select it in Settings → Media.")
    return vision[0]

def analyze_images(images, prompt, model):
    settings = load_settings()
    content = (prompt or "").strip() or "Describe the attached image in useful detail."
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content, "images": images}],
        "stream": False,
    }
    request = urllib.request.Request(
        str(settings.get("ollama_url") or OLLAMA).rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Vision model rejected the request: {detail[:500]}") from exc
    description = strip_think((result.get("message") or {}).get("content", "")).strip()
    if not description:
        raise RuntimeError("The local vision model returned an empty description")
    return description

def choose_agent_model(requested):
    if not any(marker in requested.lower() for marker in ("vision", "llava", "vl", "gemma3", "minicpm-v")):
        return requested
    settings = load_settings()
    preferred = str(settings.get("default_model") or "qwen2.5:3b")
    if not any(marker in preferred.lower() for marker in ("vision", "llava", "vl", "gemma3", "minicpm-v")):
        return preferred
    return "qwen2.5:3b"

def ollama_chat(session, model, think):
    settings = load_settings()
    mcp_servers = settings.get("mcp_servers") or []
    if mcp_servers:
        MCP_MANAGER.sync(mcp_servers)
    payload = {
        "model": model,
        "messages": session.history,
        "tools": TOOLS + MCP_MANAGER.tool_definitions(),
        "stream": False,
        "think": bool(think),
        "options": {"num_ctx": settings.get("context_size", 8192),
                    "temperature": settings.get("temperature", 0.7)},
    }
    ollama_url = str(settings.get("ollama_url") or OLLAMA).rstrip("/")
    req = urllib.request.Request(ollama_url + "/api/chat",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def _public_url(url):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only public http/https URLs are allowed")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host: {exc}")
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise ValueError("Private, local, reserved, and link-local addresses are blocked")
    return parsed.geturl()


def _plain_html(raw):
    text = re.sub(r"(?is)<(script|style|svg|noscript).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def web_search(query, limit=6):
    settings = load_settings().get("web", {})
    if not settings.get("enabled", True):
        raise RuntimeError("Web search is disabled in Settings")
    limit = max(1, min(int(limit or settings.get("max_results", 6)), 10))
    brave_key = str(settings.get("brave_api_key") or "").strip()
    if settings.get("search_provider") == "brave" and brave_key:
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": limit})
        request = urllib.request.Request(url, headers={"Accept": "application/json", "X-Subscription-Token": brave_key})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [{"title": item.get("title"), "url": item.get("url"), "snippet": item.get("description")}
                for item in data.get("web", {}).get("results", [])[:limit]]

    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(url, headers={"User-Agent": "Local-AI-Agent/2.0 (+https://github.com/fouadcyper/local-ai-agent)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read(2_000_000).decode("utf-8", "replace")
    pattern = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.I | re.S)
    results = []
    for href, title, snippet in pattern.findall(page):
        href = html.unescape(href)
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        results.append({"title": _plain_html(title), "url": href, "snippet": _plain_html(snippet)})
        if len(results) >= limit:
            break
    if not results:
        yahoo_url = "https://search.yahoo.com/search?" + urllib.parse.urlencode(
            {"p": query, "ei": "UTF-8"}
        )
        yahoo_request = urllib.request.Request(yahoo_url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        })
        with urllib.request.urlopen(yahoo_request, timeout=20) as response:
            yahoo_page = response.read(2_000_000).decode("utf-8", "replace")
        matches = re.findall(
            r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            yahoo_page, re.I | re.S
        )
        for href, title in matches:
            target = re.search(r"/RU=([^/]+)/RK=", href)
            href = urllib.parse.unquote(target.group(1)) if target else html.unescape(href)
            if not href.startswith(("http://", "https://")):
                continue
            hostname = (urllib.parse.urlparse(href).hostname or "").lower()
            if hostname.endswith(("yahoo.com", "bing.com")):
                continue
            clean_title = clip(_plain_html(title), 240)
            results.append({"title": clean_title, "url": href, "snippet": ""})
            if len(results) >= limit:
                break
    return results


def fetch_url_text(url, max_chars=16000):
    url = _public_url(url)
    max_chars = max(1000, min(int(max_chars or 16000), 50000))
    request = urllib.request.Request(url, headers={"User-Agent": "Local-AI-Agent/2.0", "Accept": "text/html,text/plain,application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        content_type = response.headers.get_content_type()
        raw = response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")
    text = _plain_html(raw) if content_type == "text/html" else raw
    return clip(f"URL: {url}\n\n{text}", max_chars)


def _mcp_result_text(result):
    if not isinstance(result, dict):
        return clip(json.dumps(result, ensure_ascii=False))
    parts = []
    for item in result.get("content") or []:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif item.get("type") in ("image", "audio"):
            parts.append(f"[{item.get('type')} content returned by MCP server]")
        elif item.get("type") == "resource_link":
            parts.append(f"[resource] {item.get('name', '')}: {item.get('uri', '')}")
    if result.get("structuredContent"):
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip() or json.dumps(result, ensure_ascii=False)
    if result.get("isError"):
        text = "[MCP tool error]\n" + text
    return clip(text)

def execute_tool(session, tc):
    name = tc.get("function", {}).get("name", "")
    args = tc.get("function", {}).get("arguments", {}) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"raw": args}

    if MCP_MANAGER.is_mcp_tool(name):
        try:
            return _mcp_result_text(MCP_MANAGER.call(name, args)), 0
        except Exception as exc:
            return f"[MCP error: {exc}]", 1

    if name == "web_search":
        try:
            results = web_search(args.get("query", ""), args.get("max_results", 6))
            return clip(json.dumps(results, ensure_ascii=False, indent=2)), 0
        except Exception as exc:
            return f"[Web search error: {exc}]", 1

    if name == "fetch_url":
        try:
            return fetch_url_text(args.get("url", ""), args.get("max_chars", 16000)), 0
        except Exception as exc:
            return f"[Fetch error: {exc}]", 1

    if name == "github_search":
        kind = args.get("type", "repositories")
        command = {"repositories": "repos", "issues": "issues", "prs": "prs", "code": "code"}.get(kind, "repos")
        fields = {"repos": "fullName,description,url,updatedAt", "issues": "repository,title,url,state,updatedAt",
                  "prs": "repository,title,url,state,updatedAt", "code": "repository,path,url"}[command]
        limit = max(1, min(int(args.get("limit") or 10), 50))
        try:
            process = subprocess.run(["gh", "search", command, args.get("query", ""), "--limit", str(limit), "--json", fields],
                                     capture_output=True, text=True, timeout=45)
            return clip(process.stdout or process.stderr), process.returncode
        except Exception as exc:
            return f"[GitHub search error: {exc}]", 1

    if name == "github_create_issue":
        command = ["gh", "issue", "create", "--repo", args.get("repo", ""), "--title", args.get("title", "")]
        if args.get("body"):
            command += ["--body", args["body"]]
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=60)
            return clip(process.stdout or process.stderr), process.returncode
        except Exception as exc:
            return f"[GitHub issue error: {exc}]", 1

    # cd tracking
    if name == "run_bash":
        cmd = (args.get("command") or "").strip()
        m = re.match(r"^cd\s+(.+)$", cmd)
        if m:
            target = m.group(1).strip().strip("\"'")
            target = os.path.expanduser(target)
            if not os.path.isabs(target):
                target = os.path.join(session.cwd, target)
            target = os.path.normpath(target)
            if os.path.isdir(target):
                session.cwd = target
                return f"[تم الانتقال إلى {target}]", 0
            return f"cd: لا يوجد مجلد: {target}", 1
        try:
            timeout = int(args.get("timeout") or CMD_TIMEOUT)
        except Exception:
            timeout = CMD_TIMEOUT
        timeout = max(1, min(timeout, 300))
        try:
            p = subprocess.run(cmd, shell=True, cwd=session.cwd,
                               capture_output=True, text=True, timeout=timeout)
            out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
            out = out.strip() or "[لا مخرجات — نجح]"
            return clip(f"[exit {p.returncode}]\n{out}"), p.returncode
        except subprocess.TimeoutExpired:
            return f"[انتهت المهلة بعد {timeout} ثانية]", 124
        except Exception as e:
            return f"[خطأ في التنفيذ: {e}]", 1

    if name == "read_file":
        path = os.path.expanduser(args.get("path", ""))
        if not os.path.isabs(path):
            path = os.path.join(session.cwd, path)
        try:
            with open(path, "r", errors="replace") as f:
                return clip(f.read()), 0
        except Exception as e:
            return f"[لا يمكن قراءة الملف: {e}]", 1

    if name == "write_file":
        path = os.path.expanduser(args.get("path", ""))
        if not os.path.isabs(path):
            path = os.path.join(session.cwd, path)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                f.write(args.get("content", ""))
            return f"[تمت الكتابة: {path} ({os.path.getsize(path)} بايت)]", 0
        except Exception as e:
            return f"[لا يمكن الكتابة: {e}]", 1

    if name == "find_files":
        pattern = args.get("pattern", "*") or "*"
        path = os.path.expanduser(args.get("path") or session.cwd)
        if not os.path.isabs(path):
            path = os.path.join(session.cwd, path)
        try:
            limit = max(1, min(int(args.get("limit") or 50), 200))
        except Exception:
            limit = 50
        cmd = (f"timeout 30 find {shlex.quote(path)} -iname {shlex.quote(pattern)} "
               f"-not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/.cache/*' "
               f"2>/dev/null | head -{limit}")
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=35)
            out = p.stdout.strip()
            return (clip(f"[{out.count(chr(10))+1} ملف]\n{out}" if out else f"[لا توجد ملفات تطابق {pattern} في {path}]"), 0)
        except Exception as e:
            return f"[خطأ في البحث: {e}]", 1

    if name == "search_content":
        pattern = args.get("pattern", "") or ""
        path = os.path.expanduser(args.get("path") or session.cwd)
        if not os.path.isabs(path):
            path = os.path.join(session.cwd, path)
        cmd = (f"timeout 30 grep -rIn --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.cache "
               f"-e {shlex.quote(pattern)} {shlex.quote(path)} 2>/dev/null | head -50")
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=35)
            out = p.stdout.strip()
            return (clip(f"[نتائج البحث عن: {pattern}]\n{out}" if out else f"[لا نتائج لـ {pattern} في {path}]"), 0)
        except Exception as e:
            return f"[خطأ في البحث: {e}]", 1

    if name == "list_directory":
        path = os.path.expanduser(args.get("path") or session.cwd)
        if not os.path.isabs(path):
            path = os.path.join(session.cwd, path)
        try:
            entries = sorted(os.listdir(path))
            lines = []
            for e in entries[:400]:
                full = os.path.join(path, e)
                mark = "/" if os.path.isdir(full) else ""
                lines.append(f"{e}{mark}")
            return clip(f"[{path}]\n" + "\n".join(lines)), 0
        except Exception as e:
            return f"[لا يمكن فتح المجلد: {e}]", 1

    return f"[أداة غير معروفة: {name}]", 1

def tool_result_msg(name, content):
    return {"role": "tool", "name": name, "content": str(content)}

def is_hard_blocked(tc):
    name = tc.get("function", {}).get("name", "")
    if name == "github_create_issue":
        return True
    if MCP_MANAGER.is_mcp_tool(name):
        return bool(MCP_MANAGER.annotations(name).get("destructiveHint"))
    cmd = (tc.get("function", {}).get("arguments", {}) or {})
    if isinstance(cmd, dict):
        cmd = cmd.get("command", "")
    return any(re.search(p, str(cmd)) for p in HARD_BLOCK)


def is_read_only_tool(name):
    if name in ("read_file", "list_directory", "find_files", "search_content",
                "web_search", "fetch_url", "github_search"):
        return True
    if MCP_MANAGER.is_mcp_tool(name):
        return bool(MCP_MANAGER.annotations(name).get("readOnlyHint"))
    return False

def trim_history(session, keep=38):
    if len(session.history) <= keep + 1:
        return
    session.history = [session.history[0]] + session.history[-keep:]

def run_agent(session, model, think, auto_approve):
    """حلقة الوكيل. تُرجع dict: final أو approval."""
    trim_history(session)
    max_steps = load_settings().get("max_steps", MAX_STEPS)
    for _ in range(max_steps):
        try:
            resp = ollama_chat(session, model, think)
        except urllib.error.URLError:
            return {"type": "error", "text": "تعذر الاتصال بـ Ollama — تأكد إنه شغال (ollama serve)"}
        except Exception as e:
            return {"type": "error", "text": f"خطأ من النموذج: {e}"}

        msg = resp.get("message", {}) or {}
        tcs = msg.get("tool_calls") or []

        if not tcs:
            text = strip_think(msg.get("content", ""))
            session.history.append({"role": "assistant", "content": text})
            stats = {"eval_count": resp.get("eval_count"),
                     "eval_duration": resp.get("eval_duration"),
                     "total_duration": resp.get("total_duration")}
            return {"type": "final", "text": text, "stats": stats, "cwd": session.cwd}

        # نسجل رسالة المساعد كما هي (مع tool_calls) بالهستوري
        assistant_entry = {"role": "assistant", "content": msg.get("content") or ""}
        assistant_entry["tool_calls"] = tcs
        session.history.append(assistant_entry)

        for i, tc in enumerate(tcs):
            name = tc.get("function", {}).get("name", "")
            args = tc.get("function", {}).get("arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            read_only = is_read_only_tool(name)
            hard = is_hard_blocked(tc)
            if read_only or (auto_approve and not hard):
                result, _ = execute_tool(session, tc)
                session.history.append(tool_result_msg(name, result))
                continue
            # تحتاج موافقة → نوقف ونطلب من الواجهة
            session.pending = {"queue": list(tcs[i:]), "model": model,
                               "think": think, "auto_approve": auto_approve}
            return {"type": "approval", "tool": {"name": name, "args": args,
                                                 "summary": tool_summary(name, args),
                                                 "hard": hard}, "cwd": session.cwd}

    session.history.append({"role": "assistant", "content": "وصلت للحد الأقصى من الخطوات."})
    return {"type": "final", "text": f"وصلت للحد الأقصى من الخطوات ({max_steps}) — وقفين هون عشان ما يعلق، اطلب مني أكمل.",
            "cwd": session.cwd}

def process_pending(session, approved):
    pending = session.pending
    if not pending:
        return {"type": "error", "text": "ما في أداة معلقة بانتظار موافقتك"}
    queue = pending["queue"]
    tc = queue.pop(0)
    name = tc.get("function", {}).get("name", "")
    auto = pending["auto_approve"]
    think = pending["think"]
    model = pending["model"]

    if approved:
        result, _ = execute_tool(session, tc)
        status = "done"
    else:
        result = "[رفض المستخدم تنفيذ هذه الأداة — لا تحاول تكرارها، أخبر المستخدم أو اقترح بديلاً]"
        status = "denied"
    session.history.append(tool_result_msg(name, result))

    # أكمل بقية الأدوات في نفس الجولة
    for i, rtc in enumerate(queue):
        rname = rtc.get("function", {}).get("name", "")
        rargs = rtc.get("function", {}).get("arguments", {}) or {}
        if isinstance(rargs, str):
            try:
                rargs = json.loads(rargs)
            except Exception:
                rargs = {}
        hard = is_hard_blocked(rtc)
        read_only = is_read_only_tool(rname)
        if read_only or (auto and not hard):
            res, _ = execute_tool(session, rtc)
            session.history.append(tool_result_msg(rname, res))
            continue
        pending["queue"] = queue[i:]
        session.pending = pending
        return {"type": "approval",
                "tool": {"name": rname, "args": rargs,
                         "summary": tool_summary(rname, rargs), "hard": hard},
                "cwd": session.cwd, "last_result": result, "last_status": status}

    session.pending = None
    nxt = run_agent(session, model, think, auto)
    nxt["last_result"] = result
    nxt["last_status"] = status
    return nxt


class TelegramBridge:
    """Single-user Telegram long-polling bridge using only the stdlib."""

    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.offset = 0
        self.token_marker = ""
        self.status = {"running": False, "connected": False, "username": "",
                       "last_error": "", "last_update": 0}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True,
                                       name="local-ai-agent-telegram")
        self.thread.start()

    def _api(self, token, method, payload=None, timeout=35):
        data = urllib.parse.urlencode(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(body.get("description") or "Telegram API error")
        return body.get("result")

    def test(self, token):
        token = (token or "").strip()
        if not token:
            current = load_settings().get("telegram", {}).get("token", "")
            token = current.strip()
        if not token:
            raise ValueError("أدخل Bot Token أولاً")
        result = self._api(token, "getMe", timeout=12)
        return {"ok": True, "username": result.get("username", ""),
                "name": result.get("first_name", ""), "bot_id": result.get("id")}

    def _send(self, token, chat_id, text):
        text = str(text or "…")
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or ["…"]
        for chunk in chunks:
            self._api(token, "sendMessage", {"chat_id": chat_id, "text": chunk}, timeout=20)

    @staticmethod
    def _approval_text(result):
        tool = result.get("tool", {})
        danger = "\nهذا أمر عالي الخطورة." if tool.get("hard") else ""
        return ("الأداة تنتظر موافقتك:\n"
                f"{tool.get('name', 'tool')}\n{tool.get('summary', '')}{danger}\n\n"
                "أرسل /approve للتنفيذ أو /deny للرفض.")

    def _handle(self, token, message, settings):
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = str(sender.get("id", ""))
        allowed = str(settings.get("telegram", {}).get("allowed_user_id", "")).strip()
        if not chat_id or not allowed or user_id != allowed:
            if chat_id:
                self._send(token, chat_id, "هذا البوت خاص. حسابك غير مصرح له.")
            return
        text = (message.get("text") or "").strip()
        if not text:
            self._send(token, chat_id, "أرسل رسالة نصية أو أمراً.")
            return
        session = get_session("telegram-" + user_id)
        if text in ("/start", "/help"):
            self._send(token, chat_id,
                       "Local AI Agent جاهز. أرسل طلبك بشكل طبيعي.\n"
                       "الأوامر: /approve موافقة، /deny رفض، /new جلسة جديدة، /status الحالة.")
            return
        if text == "/status":
            pending = "نعم" if session.pending else "لا"
            self._send(token, chat_id, f"النموذج: {settings.get('default_model')}\nأداة تنتظر الموافقة: {pending}")
            return
        if text == "/new":
            with SESSIONS_LOCK:
                SESSIONS.pop(session.id, None)
            self._send(token, chat_id, "بدأت جلسة Telegram جديدة.")
            return
        if text in ("/approve", "/deny"):
            with session.lock:
                if not session.pending:
                    result = {"type": "error", "text": "لا يوجد أمر ينتظر الموافقة."}
                else:
                    result = process_pending(session, text == "/approve")
            if result.get("type") == "approval":
                self._send(token, chat_id, self._approval_text(result))
            else:
                self._send(token, chat_id, result.get("text") or result.get("last_result") or "تم.")
            return
        if session.pending:
            self._send(token, chat_id, "يوجد أمر ينتظر قرارك. أرسل /approve أو /deny أولاً.")
            return

        with session.lock:
            if session.title in ("محادثة جديدة", "New conversation"):
                session.title = "Telegram: " + text[:48]
            prompt_key = settings.get("prompt_style", "local")
            session.prompt_key = prompt_key if prompt_key in PROMPTS else "local"
            session.history[0] = {"role": "system", "content": PROMPTS[session.prompt_key] + "\n\n" + collect_system_profile()}
            session.history.append({"role": "user", "content": text})
            result = run_agent(session, settings.get("default_model") or "qwen2.5:3b",
                               bool(settings.get("think")),
                               bool(settings.get("telegram", {}).get("auto_approve")))
        if result.get("type") == "approval":
            self._send(token, chat_id, self._approval_text(result))
        else:
            self._send(token, chat_id, result.get("text") or "لم يصل رد من النموذج.")

    def _loop(self):
        self.status["running"] = True
        while not self.stop_event.is_set():
            settings = load_settings()
            telegram = settings.get("telegram", {})
            token = str(telegram.get("token", "")).strip()
            allowed = str(telegram.get("allowed_user_id", "")).strip()
            enabled = bool(telegram.get("enabled") and token and allowed)
            if not enabled:
                self.status.update({"connected": False, "username": "", "last_error": ""})
                self.stop_event.wait(2)
                continue
            marker = token[-8:]
            if marker != self.token_marker:
                self.token_marker = marker
                # Drop queued historical updates so old commands are not replayed
                # automatically after enabling the bot or restarting the app.
                try:
                    queued = self._api(token, "getUpdates", {"offset": -1, "timeout": 0}, timeout=12)
                    self.offset = (int(queued[-1].get("update_id", 0)) + 1) if queued else 0
                except Exception:
                    self.offset = 0
            try:
                if not self.status.get("connected"):
                    me = self._api(token, "getMe", timeout=12)
                    self.status.update({"connected": True, "username": me.get("username", ""), "last_error": ""})
                updates = self._api(token, "getUpdates", {
                    "offset": self.offset,
                    "timeout": 20,
                    "allowed_updates": json.dumps(["message"]),
                }, timeout=28)
                for update in updates or []:
                    self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
                    message = update.get("message")
                    if message:
                        self.status["last_update"] = int(time.time())
                        self._handle(token, message, settings)
            except Exception as exc:
                self.status.update({"connected": False, "last_error": str(exc)[:240]})
                self.stop_event.wait(3)
        self.status["running"] = False


TELEGRAM = TelegramBridge()
TELEGRAM.start()


def connector_status():
    settings = load_settings()
    media = settings.get("media", {})
    whisper = _whisper_command(media)
    whisper_model = os.path.expanduser(str(media.get("whisper_model") or ""))
    try:
        gh = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=8)
        github_ready = gh.returncode == 0
    except Exception:
        github_ready = False
    try:
        models = discover_models().get("models", [])
        vision_ready = any(any(marker in item.get("name", "").lower() for marker in ("vision", "llava", "vl", "gemma3", "minicpm-v")) for item in models)
    except Exception:
        vision_ready = False
    return {
        "github": {"available": bool(shutil.which("gh")), "authenticated": github_ready},
        "telegram": {"configured": bool(settings.get("telegram", {}).get("enabled")), **TELEGRAM.status},
        "web_search": {"enabled": bool(settings.get("web", {}).get("enabled", True)),
                       "provider": settings.get("web", {}).get("search_provider", "duckduckgo")},
        "vision": {"ready": vision_ready, "model": settings.get("media", {}).get("vision_model", "auto")},
        "voice": {"ready": bool(whisper and os.path.isfile(whisper_model)), "command": whisper or "",
                  "model": whisper_model if os.path.isfile(whisper_model) else ""},
        "mcp": MCP_MANAGER.status(),
    }


def merge_secret_settings(current, changes):
    current = current or {}
    changes = json.loads(json.dumps(changes or {}))
    web = changes.get("web")
    if isinstance(web, dict) and not web.get("brave_api_key"):
        web.pop("brave_api_key", None)
    old_servers = {str(item.get("id")): item for item in current.get("mcp_servers", [])}
    for server in changes.get("mcp_servers", []) or []:
        old = old_servers.get(str(server.get("id")), {})
        for field in ("env", "headers"):
            incoming = server.get(field) or {}
            previous = old.get(field) or {}
            for key, value in list(incoming.items()):
                if value == "••••••••":
                    if key in previous:
                        incoming[key] = previous[key]
                    else:
                        incoming.pop(key, None)
    return changes

class Handler(BaseHTTPRequestHandler):
    server_version = "LocalAIAgent/2.0"

    def log_message(self, fmt, *args):  # تقليل الضوضاء
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def _send_file(self, path, content_type):
        try:
            with open(path, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            return self._send_html(os.path.join(HERE, "index.html"))
        if path == "/app-icon.png":
            return self._send_file(os.path.join(HERE, "assets", "local-ai-agent.png"), "image/png")
        if path == "/api/health":
            settings = load_settings()
            try:
                with urllib.request.urlopen(str(settings.get("ollama_url") or OLLAMA).rstrip("/") + "/api/tags", timeout=3) as r:
                    ok = r.status == 200
            except Exception:
                ok = False
            return self._send_json({"ok": True, "ollama": ok, "telegram": TELEGRAM.status,
                                    "mcp": MCP_MANAGER.status()})
        if path == "/api/settings":
            return self._send_json({"settings": public_settings(), "telegram_status": TELEGRAM.status})
        if path == "/api/connectors/status":
            return self._send_json({"connectors": connector_status()})
        if path == "/api/mcp/status":
            return self._send_json(MCP_MANAGER.status())
        if path == "/api/mcp/presets":
            presets = []
            for preset in MCP_PRESETS:
                item = json.loads(json.dumps(preset))
                item["command_available"] = bool(shutil.which(item.get("command", "")))
                presets.append(item)
            return self._send_json({"presets": presets})
        if path == "/api/models/discover":
            force = "refresh=1" in self.path
            return self._send_json(discover_models(force=force))
        if path == "/api/telegram/status":
            return self._send_json({"status": TELEGRAM.status})
        if path == "/api/tags":
            try:
                ollama_url = str(load_settings().get("ollama_url") or OLLAMA).rstrip("/")
                with urllib.request.urlopen(ollama_url + "/api/tags", timeout=10) as r:
                    data = json.loads(r.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return self._send_json({"models": models})
            except Exception as e:
                return self._send_json({"models": [], "error": str(e)}, 200)
        if path == "/api/sessions":
            with SESSIONS_LOCK:
                items = sorted(SESSIONS.values(), key=lambda s: s.created, reverse=True)
                return self._send_json({"sessions": [session_public(s) | {"n_messages": len([m for m in s.history if m["role"] == "user"])} for s in items]})
        if path.startswith("/api/session/"):
            sid = path.rsplit("/", 1)[-1]
            with SESSIONS_LOCK:
                s = SESSIONS.get(sid)
            if s:
                return self._send_json({"session": session_public(s)})
            return self._send_json({"error": "not found"}, 404)
        if path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        data = self._read_json()

        if self.path == "/api/settings":
            changes = data.get("settings") if isinstance(data.get("settings"), dict) else {}
            current = load_settings()
            changes = merge_secret_settings(current, changes)
            telegram = changes.get("telegram") if isinstance(changes.get("telegram"), dict) else None
            if telegram is not None and not telegram.get("token"):
                telegram.pop("token", None)
            prospective = json.loads(json.dumps(current))
            for key, value in changes.items():
                if key == "telegram" and isinstance(value, dict):
                    prospective["telegram"].update(value)
                else:
                    prospective[key] = value
            tg = prospective.get("telegram", {})
            if tg.get("enabled") and (not tg.get("token") or not str(tg.get("allowed_user_id", "")).strip()):
                return self._send_json({"error": "لتفعيل Telegram أدخل Bot Token وTelegram User ID المسموح"}, 400)
            if tg.get("enabled") and not str(tg.get("allowed_user_id", "")).strip().isdigit():
                return self._send_json({"error": "Telegram User ID يجب أن يكون رقماً فقط، وليس اسم المستخدم"}, 400)
            updated = save_settings(changes)
            if "mcp_servers" in changes:
                threading.Thread(target=MCP_MANAGER.sync, args=(updated.get("mcp_servers", []),),
                                 daemon=True, name="mcp-settings-sync").start()
            return self._send_json({"ok": True, "settings": public_settings(updated),
                                    "telegram_status": TELEGRAM.status, "mcp": MCP_MANAGER.status()})

        if self.path == "/api/mcp/test":
            server = data.get("server") if isinstance(data.get("server"), dict) else {}
            try:
                return self._send_json({"ok": True, "server": MCP_MANAGER.connect(server),
                                        "status": MCP_MANAGER.status()})
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 400)

        if self.path == "/api/mcp/disconnect":
            try:
                return self._send_json({"ok": True, "server": MCP_MANAGER.disconnect(data.get("server_id", ""))})
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 400)

        if self.path == "/api/telegram/test":
            try:
                return self._send_json(TELEGRAM.test(data.get("token", "")))
            except Exception as exc:
                return self._send_json({"ok": False, "error": str(exc)}, 400)

        if self.path == "/api/chat":
            sid = data.get("session_id") or ""
            s = get_session(sid)
            message = (data.get("message") or "").strip()
            model = data.get("model") or load_settings().get("default_model") or "qwen2.5:3b"
            try:
                images, transcripts, attachment_info = process_attachments(data.get("attachments") or [])
            except Exception as exc:
                return self._send_json({"type": "error", "text": str(exc)}, 400)
            if transcripts:
                message = (message + "\n\n" if message else "") + "\n\n".join(transcripts)
            vision_model = ""
            if images:
                try:
                    vision_model = choose_vision_model(model)
                    vision_description = analyze_images(images, message, vision_model)
                    message = ((message + "\n\n") if message else "") + (
                        "[Local vision analysis of the attached image(s)]\n" + vision_description
                    )
                    model = choose_agent_model(model)
                except Exception as exc:
                    return self._send_json({"type": "error", "text": str(exc)}, 400)
            if not message:
                return self._send_json({"type": "error", "text": "Message is empty"})
            think = bool(data.get("think"))
            auto = bool(data.get("auto_approve"))
            pkey = data.get("prompt")
            if pkey in PROMPTS:
                s.prompt_key = pkey
            s.history[0] = {"role": "system", "content": PROMPTS[s.prompt_key] + "\n\n" + collect_system_profile()}
            with s.lock:
                if s.pending is not None:
                    return self._send_json({"type": "approval",
                                             "tool": _pending_tool_payload(s.pending),
                                             "cwd": s.cwd, "resumed": True})
                if s.title in ("محادثة جديدة", "New conversation"):
                    s.title = (message[:64] + ("…" if len(message) > 64 else ""))
                user_entry = {"role": "user", "content": message}
                s.history.append(user_entry)
                result = run_agent(s, model, think, auto)
                result["session_id"] = s.id
                result["model_used"] = model
                if vision_model:
                    result["vision_model"] = vision_model
                result["attachments"] = attachment_info
            return self._send_json(result)

        if self.path == "/api/approve":
            sid = data.get("session_id") or ""
            s = get_session(sid)
            approved = bool(data.get("approved"))
            with s.lock:
                result = process_pending(s, approved)
                result["session_id"] = s.id
            return self._send_json(result)

        if self.path == "/api/session/new":
            s = Session()
            with SESSIONS_LOCK:
                SESSIONS[s.id] = s
            return self._send_json({"session_id": s.id})

        if self.path == "/api/session/delete":
            sid = data.get("session_id") or ""
            with SESSIONS_LOCK:
                SESSIONS.pop(sid, None)
            return self._send_json({"ok": True})

        return self._send_json({"error": "not found"}, 404)

def _pending_tool_payload(pending):
    tc = pending["queue"][0]
    name = tc.get("function", {}).get("name", "")
    args = tc.get("function", {}).get("arguments", {}) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    hard = is_hard_blocked(tc)
    return {"name": name, "args": args, "summary": tool_summary(name, args), "hard": hard}

def serve_forever():
    os.makedirs(HERE, exist_ok=True)
    configured_mcp = load_settings().get("mcp_servers", [])
    if configured_mcp:
        threading.Thread(target=MCP_MANAGER.sync, args=(configured_mcp,), daemon=True,
                         name="mcp-startup-sync").start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Local AI Agent يعمل على http://127.0.0.1:{PORT}")
    print(f"Ollama: {OLLAMA}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nانتهى.")

def main():
    serve_forever()

if __name__ == "__main__":
    main()
