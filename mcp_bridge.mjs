#!/usr/bin/env node
import readline from 'node:readline';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const connections = new Map();

function safeEnv(extra = {}) {
  const inherited = {};
  for (const key of ['PATH', 'HOME', 'USER', 'SHELL', 'LANG', 'LC_ALL', 'XDG_RUNTIME_DIR', 'DISPLAY', 'WAYLAND_DISPLAY']) {
    if (process.env[key]) inherited[key] = process.env[key];
  }
  return { ...inherited, ...extra };
}

async function connectServer(server) {
  const id = String(server.id || '').trim();
  if (!id) throw new Error('MCP server id is required');
  if (connections.has(id)) await disconnectServer(id);
  const client = new Client(
    { name: 'local-ai-agent', version: '2.0.0' },
    { versionNegotiation: { mode: 'auto' } },
  );
  let transport;
  if (server.transport === 'http') {
    const headers = server.headers || {};
    transport = new StreamableHTTPClientTransport(new URL(server.url), {
      requestInit: { headers },
    });
  } else {
    if (!server.command) throw new Error('Command is required for stdio MCP servers');
    transport = new StdioClientTransport({
      command: server.command,
      args: Array.isArray(server.args) ? server.args.map(String) : [],
      env: safeEnv(server.env || {}),
      stderr: 'pipe',
      maxBufferSize: 16 * 1024 * 1024,
    });
  }
  await client.connect(transport);
  connections.set(id, { client, transport, server });
  const listed = await client.listTools();
  return {
    id,
    name: server.name || id,
    serverInfo: client.getServerVersion?.() || null,
    instructions: client.getInstructions?.() || '',
    tools: listed.tools || [],
  };
}

async function disconnectServer(id) {
  const item = connections.get(id);
  if (!item) return { id, disconnected: true };
  connections.delete(id);
  try { await item.client.close(); } catch {}
  return { id, disconnected: true };
}

async function handle(message) {
  const op = message.op;
  if (op === 'connect') return connectServer(message.server || {});
  if (op === 'disconnect') return disconnectServer(String(message.server_id || ''));
  if (op === 'disconnect_all') {
    await Promise.all([...connections.keys()].map(disconnectServer));
    return { disconnected: true };
  }
  if (op === 'list') {
    const item = connections.get(String(message.server_id || ''));
    if (!item) throw new Error('MCP server is not connected');
    return item.client.listTools();
  }
  if (op === 'call') {
    const item = connections.get(String(message.server_id || ''));
    if (!item) throw new Error('MCP server is not connected');
    return item.client.callTool({ name: message.tool, arguments: message.arguments || {} });
  }
  if (op === 'status') {
    return { connected: [...connections.keys()] };
  }
  if (op === 'shutdown') {
    await Promise.all([...connections.keys()].map(disconnectServer));
    setTimeout(() => process.exit(0), 10);
    return { stopped: true };
  }
  throw new Error(`Unknown operation: ${op}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', async line => {
  let message;
  try { message = JSON.parse(line); }
  catch { return; }
  const response = { id: message.id };
  try { response.result = await handle(message); }
  catch (error) { response.error = String(error?.message || error); }
  process.stdout.write(JSON.stringify(response) + '\n');
});

process.on('SIGTERM', async () => {
  await Promise.all([...connections.keys()].map(disconnectServer));
  process.exit(0);
});
