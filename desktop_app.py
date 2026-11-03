#!/usr/bin/env python3
"""
SLMAgents 2.0 Cross-Platform Native Desktop Application (macOS, Windows, Linux).

Features:
  - Instant native OS window (< 0.3s startup) via `pywebview` (Cocoa WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux)
  - Non-blocking background startup: local FastAPI engine & first-run Hugging Face model checks run in background daemon threads
  - Automatic discovery of existing model directories (`./models`, `~/Documents/SLMAgents/models`, `~/.slm_harness/models`)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Optional

REPO_ROOT = (
    os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


SPLASH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>SLMAgents 2.0 — Starting Local AI Engine...</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      user-select: none;
    }
    .logo-box {
      width: 72px;
      height: 72px;
      border-radius: 20px;
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 50%, #0ea5e9 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 16px 40px rgba(79, 70, 229, 0.45);
      margin-bottom: 24px;
    }
    h1 { font-size: 1.5rem; font-weight: 700; margin-bottom: 8px; letter-spacing: -0.02em; }
    p { font-size: 0.92rem; color: #94a3b8; margin-bottom: 24px; }
    .spinner {
      width: 36px;
      height: 36px;
      border: 3px solid rgba(99, 102, 241, 0.25);
      border-top-color: #6366f1;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="logo-box">
    <svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
    </svg>
  </div>
  <h1>SLMAgents 2.0</h1>
  <p id="status-text">Initializing Pure-CPU Multi-Agent Studio...</p>
  <div class="spinner"></div>
</body>
</html>
"""


def detect_total_ram_gb() -> float:
    """Cross-platform physical RAM detection (macOS, Windows, Linux) using stdlib."""
    system = platform.system().lower()
    try:
        if system == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out) / (1024 ** 3)
        if system == "linux":
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
        if system == "windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return float(stat.ullTotalPhys) / (1024 ** 3)
    except Exception:
        pass
    return 8.0


def recommend_cpu_profile(ram_gb: float) -> str:
    if ram_gb < 5.5:
        return "lite"
    return "balanced"


def has_core_models(models_dir: str) -> bool:
    """Checks whether at least one core reasoning model (GGUF or ONNX) exists on disk."""
    if not models_dir or not os.path.isdir(models_dir):
        return False
    candidates = [
        os.path.join(models_dir, "gguf", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
        os.path.join(models_dir, "gguf", "Qwen_Qwen3-8B-Q4_K_M.gguf"),
        os.path.join(models_dir, "qwen3.5-0.8b-onnx"),
        os.path.join(models_dir, "qwen2.5-coder-3b-onnx"),
    ]
    for path in candidates:
        if os.path.isfile(path) or (os.path.isdir(path) and os.listdir(path)):
            return True
    return False


def resolve_models_directory() -> str:
    """Finds an existing populated models directory or falls back to `~/.slm_harness/models`."""
    env_dir = os.environ.get("SLM_MODELS_DIR")
    if env_dir and os.path.isdir(env_dir):
        return os.path.abspath(env_dir)

    search_dirs = [
        os.path.join(REPO_ROOT, "models"),
        os.path.expanduser("~/Documents/SLMAgents/models"),
        os.path.expanduser("~/SLMAgents/models"),
        os.path.expanduser("~/.slm_harness/models"),
    ]
    for candidate in search_dirs:
        if has_core_models(candidate):
            return os.path.abspath(candidate)

    if not getattr(sys, "frozen", False) and os.path.isdir(os.path.join(REPO_ROOT, "models")):
        return os.path.abspath(os.path.join(REPO_ROOT, "models"))

    user_models = os.path.expanduser("~/.slm_harness/models")
    os.makedirs(user_models, exist_ok=True)
    return user_models


def ensure_models_bootstrapped_async(models_dir: str, profile: str, skip_download: bool = False) -> None:
    """Runs model check/download in a background daemon thread so GUI startup is never blocked."""
    if has_core_models(models_dir) or skip_download:
        return

    def _bg_download() -> None:
        try:
            import download_models

            print(f"[SLMAgents Desktop] Background setup: downloading '{profile}' models to {models_dir}...")
            download_models.download_profile_models(profile=profile, models_dir=models_dir)
        except Exception as exc:
            print(f"[SLMAgents Desktop] Background model download warning: {exc}")

    threading.Thread(target=_bg_download, daemon=True, name="slm-model-bootstrap").start()


def is_server_alive(host: str, port: int, timeout: float = 0.6) -> bool:
    url = f"http://{host}:{port}/chat.html"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_free_port(preferred_ports: tuple[int, ...] = (8000, 7860, 8080)) -> int:
    for p in preferred_ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_or_attach_server(host: str, port: Optional[int]) -> tuple[int, bool]:
    """Returns `(selected_port, already_alive)` and starts Uvicorn in a daemon thread if not yet running."""
    if port is None:
        for candidate_port in (8000, 7860):
            if is_server_alive(host, candidate_port):
                return candidate_port, True
        selected_port = find_free_port((8000, 7860, 8080))
    else:
        if is_server_alive(host, port):
            return port, True
        selected_port = port

    def _serve() -> None:
        try:
            import uvicorn
            import main as slm_main

            os.chdir(REPO_ROOT)
            uvicorn.run(slm_main.app, host=host, port=selected_port, log_level="warning")
        except Exception as exc:
            print(f"[SLMAgents Desktop] Server thread error: {exc}")

    threading.Thread(target=_serve, daemon=True, name="slm-desktop-uvicorn").start()
    return selected_port, False


def run_desktop_app(
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    profile: Optional[str] = None,
    skip_download: bool = False,
    headless_open_only: bool = False,
) -> int:
    ram_gb = detect_total_ram_gb()
    active_profile = profile or os.environ.get("SLM_PROFILE") or recommend_cpu_profile(ram_gb)
    models_dir = resolve_models_directory()

    os.environ["SLM_PROFILE"] = active_profile
    os.environ["SLM_MODELS_DIR"] = models_dir

    # Start or attach to local server immediately (non-blocking)
    selected_port, already_alive = start_or_attach_server(host, port)
    studio_url = f"http://{host}:{selected_port}/chat.html"

    # Kick off non-blocking background model verification
    ensure_models_bootstrapped_async(models_dir=models_dir, profile=active_profile, skip_download=skip_download)

    if headless_open_only:
        deadline = time.time() + 15.0
        while time.time() < deadline and not is_server_alive(host, selected_port):
            time.sleep(0.25)
        webbrowser.open(studio_url)
        return 0

    # Primary: Native OS Desktop Window via `pywebview` (WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux)
    try:
        import webview  # type: ignore

        title = "SLMAgents 2.0 — Local AI & Design Studio"
        if already_alive or is_server_alive(host, selected_port):
            window = webview.create_window(title, studio_url, width=1440, height=920, min_size=(960, 640))
            webview.start()
        else:
            window = webview.create_window(title, html=SPLASH_HTML, width=1440, height=920, min_size=(960, 640))

            def _wait_and_load(win) -> None:
                deadline = time.time() + 30.0
                while time.time() < deadline:
                    if is_server_alive(host, selected_port):
                        win.load_url(studio_url)
                        return
                    time.sleep(0.25)
                win.load_url(studio_url)

            webview.start(_wait_and_load, window)
        return 0
    except Exception as exc:
        print(f"[SLMAgents Desktop] Native WebView fallback ({exc}); opening browser window.")

    # Fallback if pywebview is unavailable
    deadline = time.time() + 25.0
    while time.time() < deadline and not is_server_alive(host, selected_port):
        time.sleep(0.3)

    if platform.system().lower() == "darwin" and os.path.exists("/Applications/Google Chrome.app"):
        subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--app={studio_url}"])
    else:
        webbrowser.open(studio_url)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SLMAgents 2.0 Cross-Platform Desktop App")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind address")
    parser.add_argument("--port", type=int, default=None, help="Local port (auto-detects 8000/7860 if omitted)")
    parser.add_argument("--profile", choices=["lite", "balanced", "power"], default=None, help="CPU hardware profile")
    parser.add_argument("--skip-download", action="store_true", help="Skip automatic first-run Hugging Face model download")
    parser.add_argument("--open-only", action="store_true", help="Open in browser and return immediately")
    args = parser.parse_args(argv)
    return run_desktop_app(
        host=args.host,
        port=args.port,
        profile=args.profile,
        skip_download=args.skip_download,
        headless_open_only=args.open_only,
    )


if __name__ == "__main__":
    sys.exit(main())
