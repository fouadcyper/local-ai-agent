#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"

command -v python3 >/dev/null || { echo "Python 3 is required."; exit 1; }
command -v node >/dev/null || { echo "Node.js 24+ is required for MCP."; exit 1; }
command -v npm >/dev/null || { echo "npm is required for MCP."; exit 1; }
command -v ollama >/dev/null || { echo "Ollama is required: https://ollama.com/download"; exit 1; }

cd "$APP_DIR"
npm install --no-audit --no-fund
install -Dm644 assets/local-ai-agent.png "$DATA_DIR/icons/hicolor/512x512/apps/local-ai-agent.png"
mkdir -p "$DATA_DIR/applications"
sed "s|__APP_DIR__|$APP_DIR|g" packaging/local-ai-agent.desktop > "$DATA_DIR/applications/local-ai-agent.desktop"
chmod +x "$APP_DIR/localagent.py" "$APP_DIR/launch.sh"
echo "Installed. Open Local AI Agent from your applications menu or run: $APP_DIR/launch.sh"
