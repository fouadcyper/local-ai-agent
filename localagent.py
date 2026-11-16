#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GTK/WebKit desktop shell for Local AI Agent."""
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
INSTALLED_ICON = os.path.expanduser("~/.local/share/icons/hicolor/512x512/apps/local-ai-agent.png")
ICON = INSTALLED_ICON if os.path.isfile(INSTALLED_ICON) else os.path.join(HERE, "assets", "local-ai-agent.png")
URL = "http://127.0.0.1:7777"

def ensure_ollama():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        try:
            subprocess.Popen(["ollama", "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True
        except Exception as e:
            print(f"Could not start Ollama: {e}")
            return False

def port_open(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()

def ensure_backend():
    if port_open(7777):
        return
    import server as backend
    t = threading.Thread(target=backend.serve_forever, daemon=True)
    t.start()
    for _ in range(50):
        if port_open(7777):
            return
        time.sleep(0.1)
    print("Warning: the local backend did not open port 7777")

def main():
    ensure_ollama()
    ensure_backend()

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2, Gdk

    Gtk.init()
    if os.path.exists(ICON):
        Gtk.Window.set_default_icon_from_file(ICON)

    win = Gtk.Window(title="Local AI Agent")
    win.set_default_size(1280, 820)
    win.set_size_request(920, 600)
    if os.path.exists(ICON):
        win.set_icon_from_file(ICON)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.connect("destroy", Gtk.main_quit)

    header = Gtk.HeaderBar(title="Local AI Agent", subtitle="Private models, tools, and connections")
    header.set_show_close_button(True)
    win.set_titlebar(header)

    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_enable_developer_extras(False)
    settings.set_allow_file_access_from_file_urls(False)
    web_context = view.get_context()
    web_context.set_cache_model(WebKit2.CacheModel.WEB_BROWSER)

    view.load_uri(URL)
    win.add(view)
    win.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
