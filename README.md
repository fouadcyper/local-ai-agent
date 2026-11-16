<p align="center">
  <img src="assets/local-ai-agent.png" width="132" alt="Local AI Agent">
</p>

<h1 align="center">Local AI Agent</h1>

<p align="center">
  A polished desktop agent for local models, real tools, MCP, vision, voice, web research, and opt-in account integrations.
</p>

<p align="center">
  <strong>Local-first</strong> · <strong>English UI</strong> · <strong>Approval gates</strong> · <strong>No bundled credentials</strong>
</p>

## Download and install

> **Supported system:** Linux. The commands below are ready to copy and paste on Debian, Ubuntu, and Kali Linux.

### 1. Install the desktop requirements

~~~bash
sudo apt update
sudo apt install -y git python3 python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 ffmpeg curl
~~~

Install these two runtimes if they are not already available:

- **Ollama:** [ollama.com/download](https://ollama.com/download)
- **Node.js 24 or newer:** [nodejs.org/download](https://nodejs.org/en/download)

Confirm that everything is ready:

~~~bash
python3 --version
node --version
npm --version
ollama --version
~~~

### 2. Download Local AI Agent

~~~bash
cd ~
git clone https://github.com/fouadcyper/local-ai-agent.git
cd ~/local-ai-agent
chmod +x install.sh
./install.sh
~~~

The installer downloads the MCP client packages, installs the application icon, and adds **Local AI Agent** to the Linux applications menu.

### 3. Download the local models

The main chat model is required:

~~~bash
ollama pull qwen2.5:3b
~~~

The vision model is optional, but required for image attachments:

~~~bash
ollama pull qwen2.5vl:3b
~~~

### 4. Start the app

Open **Local AI Agent** from the applications menu, or run:

~~~bash
cd ~/local-ai-agent
./launch.sh
~~~

That is all you need for local chat, tools, MCP, web search, and image understanding. Telegram, GitHub, Brave, and other account integrations are enabled later from **Settings & connections** using your own login or token.

<details>
<summary><strong>Browser-only mode</strong></summary>

~~~bash
cd ~/local-ai-agent
python3 server.py
~~~

Then open [http://127.0.0.1:7777](http://127.0.0.1:7777).

</details>

<details>
<summary><strong>Updating an existing installation</strong></summary>

~~~bash
cd ~/local-ai-agent
git pull
./install.sh
~~~

</details>

<details>
<summary><strong>Common installation problems</strong></summary>

- **Ollama offline:** run <code>ollama serve</code>, then reopen the app.
- **No model appears:** run <code>ollama list</code> and pull <code>qwen2.5:3b</code>.
- **Desktop window does not open:** verify that <code>python3-gi</code>, GTK 3, and WebKitGTK 4.1 are installed.
- **MCP is unavailable:** verify that <code>node --version</code> reports version 24 or newer, then run <code>npm install</code> inside the project.
- **Voice notes are unavailable:** install whisper.cpp and select its binary and model under **Settings → Vision & Voice**.

</details>

## What it does

Local AI Agent runs the language model through Ollama on your computer and gives it a practical, reviewable tool layer.

- Chat with any tool-capable Ollama model.
- Attach images and analyze them with an installed vision model.
- Attach voice notes and transcribe them locally with whisper.cpp.
- Search the web and read public pages with private-network/SSRF guards.
- Connect MCP servers over stdio or Streamable HTTP using the official TypeScript client SDK.
- Discover Ollama models and local GGUF, GGML, SafeTensors, ONNX, TFLite, and PyTorch weights.
- Use built-in file, shell, GitHub search, and GitHub issue tools.
- Connect a private Telegram bot restricted to one numeric user ID.
- Review every write, shell, destructive MCP, or outward-facing action before it runs.
- Use the responsive web interface or the GTK/WebKit desktop window.

## Built-in MCP presets

The settings panel includes ready-to-add presets for:

- Official Filesystem server in read-only mode
- Official Memory server
- Official Sequential Thinking server
- Official Fetch server
- Official Git server
- Official GitHub MCP server with OAuth/device login
- Brave Search MCP server

Any other standard MCP server can be added with a command and environment variables, or a Streamable HTTP URL and headers. Secrets are masked when settings are returned to the UI.

## Requirements reference

| Component | Required | Purpose |
|---|---:|---|
| Linux + Python 3.11+ | Yes | Local backend and desktop shell |
| Ollama + qwen2.5:3b | Yes | Local chat and agent tools |
| Node.js 24+ + npm | Yes for MCP | MCP client and stdio servers |
| GTK 3 + WebKitGTK 4.1 | Yes for desktop | Native Linux window |
| qwen2.5vl:3b | Optional | Image understanding |
| ffmpeg + whisper.cpp model | Optional | Voice-note transcription |
| gh, Docker, uvx, API keys | Optional | Their matching integrations |

## Configure integrations

Open **Settings & connections**:

1. **Models** scans installed runtimes and model locations.
2. **Vision & Voice** selects the local vision model and whisper.cpp paths.
3. **Web Search** works without a key, or can use a user-supplied Brave key.
4. **MCP Servers** adds presets or custom stdio/HTTP servers and tests them live.
5. **Integrations** shows which capabilities are ready and authenticated.
6. **Telegram** accepts the user's own bot token and a single allowed numeric user ID.

GitHub uses the local gh login:

~~~bash
gh auth login
~~~

No token, account login, or API key is shipped in this repository.

## Safety model

Read-only tools may run automatically. File writes, shell commands, account mutations, and MCP tools marked destructive stop for approval. A small set of high-risk shell patterns remains manually gated even when auto approval is enabled.

The backend binds to <code>127.0.0.1</code>. Settings are stored at:

~~~text
~/.config/local-ai-agent/settings.json
~~~

The directory is created with mode <code>700</code> and the settings file with mode <code>600</code>. Telegram tokens, API keys, MCP environment secrets, and authorization headers are masked in API responses.

Local-first does not mean offline-only: the model conversation runs locally, while explicitly enabled web, Telegram, GitHub, or remote MCP tools send the requests required by those services.

## Architecture

~~~text
GTK/WebKit or browser
        │
Python local API + approval engine
        ├── Ollama chat and vision
        ├── whisper.cpp voice transcription
        ├── built-in filesystem, shell, web, and GitHub tools
        └── Node MCP bridge → stdio / Streamable HTTP servers
~~~

## Development checks

~~~bash
python3 -m py_compile server.py agent_settings.py mcp_manager.py localagent.py
node --check mcp_bridge.mjs
sed -n '/<script>/,/<\/script>/p' index.html | sed '1d;$d' | node --check -
~~~

## Honest limitations

- Local 3–4B models are useful for everyday automation but less reliable than large hosted models on complex planning.
- Vision requires a separate Ollama vision model and may be slower on CPU-only systems.
- Voice input requires a local whisper.cpp binary and model.
- Third-party MCP servers and account integrations have their own permissions and security boundaries.
- Text-to-speech paths are configurable, but spoken output requires a compatible Piper runtime.

## Contributing

Issues and focused pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT © Fouad
