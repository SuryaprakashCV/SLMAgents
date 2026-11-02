#!/usr/bin/env python3
"""
Cross-Platform Desktop Installer & Bundle Builder for SLMAgents 2.0.

Builds:
  1. Native macOS Application Bundle (`dist/SLMAgents.app`) + `.dmg` disk image
  2. Portable Cross-Platform Release Archives (`macOS .zip`, `Windows .zip`, `Linux .tar.gz`)
     that auto-download quantized model weights from Hugging Face Hub on first launch
  3. Optional Standalone Frozen Executables (`--freeze` via PyInstaller: `SLMAgents.exe` / `SLMAgents.app` / `SLMAgents`)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import zipfile
import tarfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_DIR = os.path.join(REPO_ROOT, "dist")


INFO_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>SLMAgents 2.0</string>
    <key>CFBundleExecutable</key>
    <string>SLMAgents</string>
    <key>CFBundleIdentifier</key>
    <string>ai.slmagents.desktop</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>SLMAgents</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0.0</string>
    <key>CFBundleVersion</key>
    <string>2.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""


def build_macos_app_bundle(output_dir: str = DIST_DIR, create_dmg: bool = False) -> str:
    """Creates a native macOS `SLMAgents.app` bundle in `dist/SLMAgents.app`."""
    os.makedirs(output_dir, exist_ok=True)
    app_path = os.path.join(output_dir, "SLMAgents.app")
    if os.path.exists(app_path):
        shutil.rmtree(app_path)

    contents_dir = os.path.join(app_path, "Contents")
    macos_dir = os.path.join(contents_dir, "MacOS")
    resources_dir = os.path.join(contents_dir, "Resources")
    os.makedirs(macos_dir, exist_ok=True)
    os.makedirs(resources_dir, exist_ok=True)

    with open(os.path.join(contents_dir, "Info.plist"), "w", encoding="utf-8") as f:
        f.write(INFO_PLIST_TEMPLATE)

    # Copy portable runtime files into Contents/Resources/app so SLMAgents.app works standalone anywhere
    embedded_app_dir = os.path.join(resources_dir, "app")
    for abs_p, rel_p in _iter_bundle_files():
        dest_p = os.path.join(embedded_app_dir, rel_p)
        os.makedirs(os.path.dirname(dest_p), exist_ok=True)
        shutil.copy2(abs_p, dest_p)

    launcher_path = os.path.join(macos_dir, "SLMAgents")
    launcher_script = f"""#!/usr/bin/env bash
set -e
mkdir -p "$HOME/.slm_harness"
APP_RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
REPO_FALLBACK="{REPO_ROOT}"

if [ -d "$REPO_FALLBACK/.venv" ] && [ -f "$REPO_FALLBACK/desktop_app.py" ]; then
    WORK_DIR="$REPO_FALLBACK"
elif [ -f "$APP_RESOURCES/app/desktop_app.py" ]; then
    WORK_DIR="$APP_RESOURCES/app"
else
    WORK_DIR="$REPO_FALLBACK"
fi

cd "$WORK_DIR"
if [ -x "$WORK_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$WORK_DIR/.venv/bin/python"
elif [ -x "$HOME/.slm_harness/.venv/bin/python" ]; then
    PYTHON_BIN="$HOME/.slm_harness/.venv/bin/python"
else
    SYS_PY="$(command -v python3 || echo /usr/bin/python3)"
    "$SYS_PY" -m venv "$HOME/.slm_harness/.venv"
    "$HOME/.slm_harness/.venv/bin/python" -m pip install --upgrade pip
    "$HOME/.slm_harness/.venv/bin/python" -m pip install -r "$WORK_DIR/requirements.txt"
    PYTHON_BIN="$HOME/.slm_harness/.venv/bin/python"
fi

export PYTHONPATH="$WORK_DIR"
exec "$PYTHON_BIN" "$WORK_DIR/desktop_app.py" --open-only >> "$HOME/.slm_harness/desktop_app.log" 2>&1
"""
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(launcher_script)
    os.chmod(launcher_path, os.stat(launcher_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"[Build] Created macOS Application Bundle: {app_path}")

    if create_dmg and platform.system().lower() == "darwin":
        dmg_path = os.path.join(output_dir, "SLMAgents-2.0-macOS.dmg")
        if os.path.exists(dmg_path):
            os.remove(dmg_path)
        try:
            subprocess.run(
                [
                    "hdiutil",
                    "create",
                    "-volname",
                    "SLMAgents 2.0",
                    "-srcfolder",
                    app_path,
                    "-ov",
                    "-format",
                    "UDZO",
                    dmg_path,
                ],
                check=True,
            )
            print(f"[Build] Created macOS Disk Image: {dmg_path}")
        except Exception as exc:
            print(f"[Build] DMG creation warning: {exc}")

    return app_path


def _iter_bundle_files():
    """Yields `(abs_path, rel_path)` for all source/runtime files (excluding `.venv`, `.git`, `models`, `dist`)."""
    exclude_dirs = {
        ".git",
        ".venv",
        "__pycache__",
        "models",
        "leaderboard",
        "android_app",
        "dist",
        "build",
        ".kilo",
        "scratch",
        "node_modules",
    }
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".DS_Store")]
        for fn in files:
            if fn.endswith((".pyc", ".pyo", ".DS_Store", ".log")):
                continue
            abs_p = os.path.join(root, fn)
            rel_p = os.path.relpath(abs_p, REPO_ROOT)
            yield abs_p, rel_p


def build_cross_platform_archives(output_dir: str = DIST_DIR) -> list[str]:
    """Creates downloadable portable release packages for macOS, Windows, and Linux."""
    os.makedirs(output_dir, exist_ok=True)
    created: list[str] = []

    files = list(_iter_bundle_files())

    # 1. Windows & macOS portable .zip
    for target_os in ("macOS", "Windows"):
        zip_path = os.path.join(output_dir, f"SLMAgents-2.0-{target_os}.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for abs_p, rel_p in files:
                zf.write(abs_p, arcname=os.path.join("SLMAgents", rel_p))
        created.append(zip_path)
        print(f"[Build] Packaged {target_os} downloadable archive: {zip_path}")

    # 2. Linux portable .tar.gz
    tar_path = os.path.join(output_dir, "SLMAgents-2.0-Linux.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tf:
        for abs_p, rel_p in files:
            tf.add(abs_p, arcname=os.path.join("SLMAgents", rel_p))
    created.append(tar_path)
    print(f"[Build] Packaged Linux downloadable archive: {tar_path}")

    return created


def build_pyinstaller_binary() -> int:
    """Freezes `desktop_app.py` into a standalone OS executable (`SLMAgents.exe` / `SLMAgents.app` / `SLMAgents`)."""
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name=SLMAgents",
        "--windowed",
        f"--add-data=website{os.pathsep}website",
        f"--add-data=slm_harness{os.pathsep}slm_harness",
        f"--add-data=download_models.py{os.pathsep}.",
        f"--add-data=main.py{os.pathsep}.",
        "desktop_app.py",
    ]
    print(f"[Build] Running PyInstaller: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=REPO_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SLMAgents 2.0 Cross-Platform Desktop Packages")
    parser.add_argument("--dmg", action="store_true", help="Also build macOS .dmg installer")
    parser.add_argument("--archives", action="store_true", help="Build portable macOS/Windows/Linux archives")
    parser.add_argument("--freeze", action="store_true", help="Freeze standalone binary via PyInstaller")
    args = parser.parse_args()

    if platform.system().lower() == "darwin":
        build_macos_app_bundle(create_dmg=args.dmg)

    if args.archives:
        build_cross_platform_archives()

    if args.freeze:
        return build_pyinstaller_binary()

    return 0


if __name__ == "__main__":
    sys.exit(main())
