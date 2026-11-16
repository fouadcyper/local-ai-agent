#!/usr/bin/env python3
"""Persistent settings and local-model discovery for Local AI Agent."""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.error
import urllib.request


HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".config", "local-ai-agent")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULTS = {
    "app_name": "Local AI Agent",
    "language": "en",
    "theme": "dark",
    "default_model": "qwen2.5:3b",
    "prompt_style": "local",
    "think": False,
    "auto_approve": False,
    "max_steps": 12,
    "context_size": 8192,
    "temperature": 0.7,
    "ollama_url": "http://127.0.0.1:11434",
    "model_search_paths": [
        "~/.ollama/models",
        "~/.cache/huggingface/hub",
        "~/.cache/lm-studio/models",
        "~/.lmstudio/models",
        "~/.local/share/Jan/models",
        "~/.local/share/jan/models",
        "~/.cache/gpt4all",
        "~/.local/share/nomic.ai/GPT4All",
        "~/models",
        "~/Models",
    ],
    "telegram": {
        "enabled": False,
        "token": "",
        "allowed_user_id": "",
        "auto_approve": False,
    },
    "media": {
        "max_upload_mb": 24,
        "vision_model": "auto",
        "whisper_command": "auto",
        "whisper_model": "~/.local/share/local-ai-agent/models/whisper/ggml-base.bin",
        "tts_enabled": False,
        "piper_model": "~/.local/share/local-ai-agent/models/piper/voice.onnx",
    },
    "web": {
        "enabled": True,
        "search_provider": "duckduckgo",
        "brave_api_key": "",
        "max_results": 6,
    },
    "mcp_servers": [],
    "connectors": {
        "github_enabled": True,
    },
}

_lock = threading.RLock()
_scan_lock = threading.Lock()
_scan_cache = {"at": 0.0, "data": None}


def _deep_merge(base, incoming):
    result = copy.deepcopy(base)
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings():
    with _lock:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError, TypeError):
            raw = {}
        return _deep_merge(DEFAULTS, raw)


def save_settings(changes):
    with _lock:
        current = load_settings()
        updated = _deep_merge(current, changes or {})
        updated["max_steps"] = max(1, min(int(updated.get("max_steps", 12)), 30))
        updated["context_size"] = max(1024, min(int(updated.get("context_size", 8192)), 131072))
        updated["temperature"] = max(0.0, min(float(updated.get("temperature", 0.7)), 2.0))
        telegram = updated.setdefault("telegram", {})
        telegram["allowed_user_id"] = str(telegram.get("allowed_user_id", "")).strip()
        telegram["token"] = str(telegram.get("token", "")).strip()
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, SETTINGS_FILE)
        os.chmod(SETTINGS_FILE, 0o600)
        return updated


def public_settings(settings=None):
    data = copy.deepcopy(settings or load_settings())
    telegram = data.setdefault("telegram", {})
    token = telegram.pop("token", "")
    telegram["has_token"] = bool(token)
    telegram["token_hint"] = ("••••" + token[-5:]) if token else ""
    web = data.setdefault("web", {})
    brave = web.pop("brave_api_key", "")
    web["has_brave_api_key"] = bool(brave)
    web["brave_key_hint"] = ("••••" + brave[-4:]) if brave else ""
    for server in data.get("mcp_servers", []):
        env = server.get("env") or {}
        for key in list(env):
            if any(word in key.upper() for word in ("TOKEN", "KEY", "SECRET", "PASSWORD")) and env[key]:
                env[key] = "••••••••"
        headers = server.get("headers") or {}
        for key in list(headers):
            if key.lower() in ("authorization", "x-api-key") and headers[key]:
                headers[key] = "••••••••"
    return data


def _fetch_json(url, timeout=1.5):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _brand_for(name):
    value = name.lower()
    brands = [
        (("qwen",), "Alibaba Qwen"),
        (("llama", "codellama"), "Meta"),
        (("mistral", "mixtral", "codestral"), "Mistral AI"),
        (("gemma",), "Google"),
        (("deepseek", "janus"), "DeepSeek"),
        (("phi-", "phi3", "phi4"), "Microsoft"),
        (("command-r", "aya-"), "Cohere"),
        (("falcon",), "TII"),
        (("yi-",), "01.AI"),
        (("whisper", "clip"), "OpenAI"),
        (("stable-diffusion", "sdxl", "flux"), "Image model"),
        (("piper",), "Piper TTS"),
    ]
    for needles, brand in brands:
        if any(needle in value for needle in needles):
            return brand
    return "Local model"


def _size_label(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


def _runtime_models(settings):
    runtimes, models = [], []
    ollama = str(settings.get("ollama_url") or DEFAULTS["ollama_url"]).rstrip("/")
    try:
        data = _fetch_json(ollama + "/api/tags", 2.5)
        listed = data.get("models", [])
        runtimes.append({"name": "Ollama", "url": ollama, "online": True, "count": len(listed)})
        for item in listed:
            name = item.get("name") or item.get("model")
            if name:
                models.append({
                    "id": name,
                    "name": name,
                    "runtime": "Ollama",
                    "brand": _brand_for(name),
                    "size": item.get("size", 0),
                    "size_label": _size_label(item.get("size", 0)),
                    "runnable": True,
                })
    except Exception as exc:
        runtimes.append({"name": "Ollama", "url": ollama, "online": False, "error": str(exc)})

    endpoints = [
        ("LM Studio", "http://127.0.0.1:1234"),
        ("llama.cpp / LocalAI", "http://127.0.0.1:8080"),
        ("vLLM / LocalAI", "http://127.0.0.1:8000"),
        ("Jan", "http://127.0.0.1:1337"),
        ("text-generation-webui", "http://127.0.0.1:5000"),
    ]
    for runtime, base in endpoints:
        try:
            data = _fetch_json(base + "/v1/models", 0.7)
            listed = data.get("data", []) if isinstance(data, dict) else []
            runtimes.append({"name": runtime, "url": base, "online": True, "count": len(listed)})
            for item in listed:
                name = item.get("id") if isinstance(item, dict) else str(item)
                if name:
                    models.append({"id": f"{runtime}:{name}", "name": name, "runtime": runtime,
                                   "brand": _brand_for(name), "size": 0, "size_label": "—", "runnable": True})
        except Exception:
            runtimes.append({"name": runtime, "url": base, "online": False})
    return runtimes, models


def _weight_files(settings):
    extensions = {".gguf", ".ggml", ".safetensors", ".onnx", ".tflite", ".mlmodel", ".pth", ".pt", ".ckpt", ".bin"}
    ignored = {".git", "node_modules", "Trash", ".Trash", "__pycache__"}
    results, seen = [], set()
    paths = list(settings.get("model_search_paths") or [])
    # Well-known application model stores not always present in defaults.
    paths.extend([
        "~/text-generation-webui/models", "~/stable-diffusion-webui/models", "~/ComfyUI/models",
        "~/FOUAD-AI-RECOVERY/AI-MODELS", "~/FOUAD-AI-RECOVERY/VOICE-MODELS",
        "~/models/Janus-Pro-7B", "~/.local/share/Jan/data/llamacpp/models",
        "~/.cache/llmfit/models", "~/image-ai/models",
    ])
    for configured in paths:
        root = os.path.realpath(os.path.expanduser(str(configured)))
        if not os.path.isdir(root) or root in seen:
            continue
        seen.add(root)
        visited = 0
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]
            visited += len(files)
            if visited > 12000 or len(results) >= 500:
                break
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in extensions:
                    continue
                path = os.path.join(base, filename)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                # Ignore tiny vocabulary/test artifacts that are not model weights.
                minimum = 10 * 1024 * 1024 if ext == ".bin" else 1024 * 1024
                if size < minimum:
                    continue
                results.append({"name": filename, "path": path, "format": ext[1:].upper(),
                                "brand": _brand_for(filename), "size": size,
                                "size_label": _size_label(size), "runnable": False})
                if len(results) >= 500:
                    break
    results.sort(key=lambda item: item["size"], reverse=True)
    return results


def discover_models(force=False):
    now = time.time()
    with _scan_lock:
        if not force and _scan_cache["data"] and now - _scan_cache["at"] < 45:
            return _scan_cache["data"]
        settings = load_settings()
        runtimes, models = _runtime_models(settings)
        files = _weight_files(settings)
        data = {
            "runtimes": runtimes,
            "models": models,
            "files": files,
            "scanned_at": int(time.time()),
            "summary": {
                "runnable": len(models),
                "files": len(files),
                "online_runtimes": sum(1 for item in runtimes if item.get("online")),
                "total_size": sum(item.get("size", 0) for item in files),
                "total_size_label": _size_label(sum(item.get("size", 0) for item in files)),
            },
        }
        _scan_cache.update({"at": time.time(), "data": data})
        return data
