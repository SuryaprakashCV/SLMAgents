#!/usr/bin/env python3
"""
SLMAgents 2.0 Cross-Platform Desktop Application Launcher (macOS, Windows, Linux).

Features:
  - Runs 100% locally on user hardware (macOS Apple Silicon/Intel, Windows x64/ARM64, Linux x64/ARM64)
  - Zero cloud hosting bills: pulls quantized GGUF/ONNX weights on first run from Hugging Face Hub CDN
  - Hardware-aware profile auto-selection (`lite` / `balanced` / `power`) based on system RAM
  - Opens a native frameless Desktop App window (`pywebview` or OS native `--app` window)
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
    if ram_gb < 14.0:
        return "balanced"
    return "balanced"


def resolve_models_directory() -> str:
    """Selects writable models directory (`./models` in repo or `~/.slm_harness/models` in packaged app)."""
    env_dir = os.environ.get("SLM_MODELS_DIR")
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return os.path.abspath(env_dir)

    repo_models = os.path.join(REPO_ROOT, "models")
    if not getattr(sys, "frozen", False) and os.path.isdir(repo_models):
        return repo_models

    user_models = os.path.expanduser("~/.slm_harness/models")
    os.makedirs(user_models, exist_ok=True)
    return user_models


def has_core_models(models_dir: str) -> bool:
    """Checks whether at least one core reasoning model (GGUF or ONNX) exists on disk."""
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


def ensure_models_bootstrapped(models_dir: str, profile: str, skip_download: bool = False) -> None:
    if has_core_models(models_dir) or skip_download:
        return
    print(f"[SLMAgents Desktop] First-run setup: downloading quantized '{profile}' models from Hugging Face Hub...")
    print(f"[SLMAgents Desktop] Destination: {models_dir}")
    try:
        import download_models

        download_models.download_profile_models(profile=profile, models_dir=models_dir)
    except Exception as exc:
        print(f"[SLMAgents Desktop] Model auto-download warning: {exc}")
        print("[SLMAgents Desktop] Continuing startup (you can download models anytime via `./slm models --download balanced`).")


def is_server_alive(host: str, port: int, timeout: float = 0.8) -> bool:
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


def find_native_app_browser() -> Optional[str]:
    """Finds an installed Chromium-based browser on macOS, Windows, or Linux to render a frameless `--app=` window."""
    system = platform.system().lower()
    candidates = []
    if system == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "windows":
        prog_files = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for base in prog_files:
            if not base:
                continue
            candidates.extend(
                [
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                ]
            )
    else:
        for bin_name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"):
            found = shutil.which(bin_name)
            if found:
                candidates.append(found)

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def launch_desktop_window(url: str, title: str = "SLMAgents 2.0 — Pure-CPU AI & Design Studio") -> Optional[subprocess.Popen]:
    """
    Opens SLMAgents in a native desktop window:
      1. `pywebview` native OS WebView if installed
      2. Native frameless `--app=<url>` window via Chrome / Edge / Brave
      3. Default system browser fallback
    """
    try:
        import webview  # type: ignore

        print(f"[SLMAgents Desktop] Opening native pywebview window at {url}")
        webview.create_window(title, url, width=1440, height=920, min_size=(960, 640))
        webview.start()
        return None
    except ImportError:
        pass

    browser_bin = find_native_app_browser()
    if browser_bin:
        app_profile_dir = os.path.expanduser("~/.slm_harness/desktop_window_profile")
        os.makedirs(app_profile_dir, exist_ok=True)
        cmd = [
            browser_bin,
            f"--app={url}",
            f"--user-data-dir={app_profile_dir}",
            "--window-size=1440,920",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        print(f"[SLMAgents Desktop] Opening frameless desktop window via {os.path.basename(browser_bin)}")
        try:
            return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    print(f"[SLMAgents Desktop] Opening studio in system browser: {url}")
    webbrowser.open(url)
    return None


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

    print("==================================================================")
    print("  SLMAgents 2.0 — Cross-Platform Desktop AI & Design Studio")
    print(f"  OS: {platform.system()} {platform.release()} ({platform.machine()}) | RAM: {ram_gb:.1f} GB")
    print(f"  Profile: {active_profile} | Models Dir: {models_dir}")
    print("==================================================================")

    ensure_models_bootstrapped(models_dir=models_dir, profile=active_profile, skip_download=skip_download)

    # Reuse already-running local SLMAgents server if alive on 8000 or 7860
    reuse_server = False
    selected_port = port
    if selected_port is None:
        for candidate_port in (8000, 7860):
            if is_server_alive(host, candidate_port):
                selected_port = candidate_port
                reuse_server = True
                break
        if selected_port is None:
            selected_port = find_free_port((8000, 7860, 8080))
    else:
        reuse_server = is_server_alive(host, selected_port)

    server_thread: Optional[threading.Thread] = None
    if not reuse_server:
        import uvicorn
        import main as slm_main

        def _serve() -> None:
            os.chdir(REPO_ROOT)
            uvicorn.run(slm_main.app, host=host, port=selected_port, log_level="warning")

        server_thread = threading.Thread(target=_serve, daemon=True, name="slm-desktop-uvicorn")
        server_thread.start()

        print(f"[SLMAgents Desktop] Starting local AI Engine on http://{host}:{selected_port} ...")
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if is_server_alive(host, selected_port):
                break
            time.sleep(0.35)
    else:
        print(f"[SLMAgents Desktop] Attached to running SLMAgents Engine on http://{host}:{selected_port}")

    studio_url = f"http://{host}:{selected_port}/chat.html"
    win_proc = launch_desktop_window(studio_url)

    if headless_open_only:
        return 0

    try:
        if win_proc is not None:
            # Wait while the frameless desktop window is open
            win_proc.wait()
            if server_thread is not None:
                # Keep alive if background server was spawned and browser closed immediately by handoff
                while server_thread.is_alive():
                    time.sleep(1.0)
        elif server_thread is not None:
            print("[SLMAgents Desktop] Studio is running. Press Ctrl+C in this terminal to stop.")
            while server_thread.is_alive():
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[SLMAgents Desktop] Shutting down cleanly.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SLMAgents 2.0 Cross-Platform Desktop App")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind address")
    parser.add_argument("--port", type=int, default=None, help="Local port (auto-detects 8000/7860 if omitted)")
    parser.add_argument("--profile", choices=["lite", "balanced", "power"], default=None, help="CPU hardware profile")
    parser.add_argument("--skip-download", action="store_true", help="Skip automatic first-run Hugging Face model download")
    parser.add_argument("--open-only", action="store_true", help="Open desktop window and return immediately if server is already running")
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
