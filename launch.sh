#!/usr/bin/env bash
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$APP_DIR/localagent.py"
