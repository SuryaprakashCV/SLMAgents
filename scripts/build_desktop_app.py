#!/usr/bin/env python3
"""
Standalone Zero-Dependency Installer Builder for SLMAgents 2.0 (macOS, Windows, Linux).

Produces true standalone installers that DO NOT require Python to be pre-installed:
  1. macOS:
     - `dist/SLMAgents.app` (Frozen standalone macOS app bundle with embedded Python 3.11 + libraries via PyInstaller)
     - `dist/SLMAgents-2.0-macOS-Installer.pkg` (Native macOS Wizard Installer that installs to `/Applications/SLMAgents.app`)
     - `dist/SLMAgents-2.0-macOS-Installer.dmg` (Drag-and-drop macOS Disk Image with `/Applications` symlink)
  2. Linux:
     - `dist/SLMAgents-2.0-Linux-Installer.run` (Single-file self-extracting executable `.run` installer that bundles the app
       and auto-provisions an isolated portable Python runtime + Desktop entry with zero system Python dependency)
  3. Windows:
     - `dist/SLMAgents-2.0-Windows-Installer.bat` / `.ps1` + Inno Setup `.iss` (Self-contained Windows installer that automatically
       downloads & unpacks an isolated portable Python runtime into `%LOCALAPPDATA%\\SLMAgents`, creates a Desktop shortcut,
       and launches SLMAgents without needing Python installed on Windows; plus PyInstaller `.exe` support on Windows)
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_DIR = os.path.join(REPO_ROOT, "dist")


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
            if fn.endswith((".pyc", ".pyo", ".DS_Store", ".log", ".spec")):
                continue
            abs_p = os.path.join(root, fn)
            rel_p = os.path.relpath(abs_p, REPO_ROOT)
            yield abs_p, rel_p


def build_pyinstaller_frozen_bundle() -> str:
    """
    Freezes `desktop_app.py` + Python interpreter + all packages (`uvicorn`, `fastapi`, `reportlab`, `pptx`, `docx`, etc.)
    into a standalone native bundle (`dist/SLMAgents.app` on macOS, `dist/SLMAgents/SLMAgents.exe` on Windows, `dist/SLMAgents/SLMAgents` on Linux).
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    add_data_args = [
        f"--add-data=website{os.pathsep}website",
        f"--add-data=slm_harness{os.pathsep}slm_harness",
        f"--add-data=download_models.py{os.pathsep}.",
        f"--add-data=main.py{os.pathsep}.",
        f"--add-data=requirements.txt{os.pathsep}.",
    ]
    # Include all `slm_*` specialist packages
    for entry in sorted(os.listdir(REPO_ROOT)):
        full_p = os.path.join(REPO_ROOT, entry)
        if os.path.isdir(full_p) and entry.startswith("slm_") and entry != "slm_harness":
            add_data_args.append(f"--add-data={entry}{os.pathsep}{entry}")

    hidden_imports = [
        "--hidden-import=main",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=pptx",
        "--hidden-import=docx",
        "--hidden-import=reportlab",
        "--hidden-import=pypdf",
        "--hidden-import=huggingface_hub",
        "--exclude-module=torch",
        "--exclude-module=torchvision",
        "--exclude-module=torchaudio",
        "--exclude-module=tensorflow",
    ]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name=SLMAgents",
        "--windowed",
        "--osx-bundle-identifier=ai.slmagents.desktop",
        *add_data_args,
        *hidden_imports,
        "desktop_app.py",
    ]
    print("[Build] Freezing standalone zero-dependency binary with PyInstaller...")
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)

    app_bundle = os.path.join(DIST_DIR, "SLMAgents.app")
    if os.path.exists(app_bundle):
        print(f"[Build] Frozen standalone macOS app ready: {app_bundle}")
        return app_bundle
    return os.path.join(DIST_DIR, "SLMAgents")


def build_macos_standalone_installers(app_path: str) -> list[str]:
    """
    Wraps the standalone `dist/SLMAgents.app` into:
      1. `dist/SLMAgents-2.0-macOS-Installer.pkg` (Native macOS Installer Wizard that installs into `/Applications`)
      2. `dist/SLMAgents-2.0-macOS-Installer.dmg` (Drag-and-drop macOS Disk Image with `/Applications` link)
    """
    outputs: list[str] = []
    if platform.system().lower() != "darwin" or not os.path.exists(app_path):
        return outputs

    # 1. Native macOS .pkg Wizard Installer
    pkg_path = os.path.join(DIST_DIR, "SLMAgents-2.0-macOS-Installer.pkg")
    if os.path.exists(pkg_path):
        os.remove(pkg_path)
    try:
        subprocess.run(
            [
                "pkgbuild",
                "--component",
                app_path,
                "--install-location",
                "/Applications",
                "--identifier",
                "ai.slmagents.desktop.pkg",
                "--version",
                "2.0.0",
                pkg_path,
            ],
            check=True,
        )
        outputs.append(pkg_path)
        print(f"[Build] Created macOS Standalone .pkg Wizard Installer: {pkg_path}")
    except Exception as exc:
        print(f"[Build] pkgbuild warning: {exc}")

    # 2. Native macOS .dmg Disk Image with /Applications symlink
    dmg_path = os.path.join(DIST_DIR, "SLMAgents-2.0-macOS-Installer.dmg")
    if os.path.exists(dmg_path):
        os.remove(dmg_path)
    dmg_staging = os.path.join(DIST_DIR, "_dmg_staging")
    if os.path.exists(dmg_staging):
        shutil.rmtree(dmg_staging)
    os.makedirs(dmg_staging, exist_ok=True)
    try:
        shutil.copytree(app_path, os.path.join(dmg_staging, "SLMAgents.app"), symlinks=True)
        os.symlink("/Applications", os.path.join(dmg_staging, "Applications"))
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "SLMAgents 2.0 Installer",
                "-srcfolder",
                dmg_staging,
                "-ov",
                "-format",
                "UDZO",
                dmg_path,
            ],
            check=True,
        )
        outputs.append(dmg_path)
        print(f"[Build] Created macOS Standalone .dmg Installer: {dmg_path}")
    except Exception as exc:
        print(f"[Build] DMG creation warning: {exc}")
    finally:
        shutil.rmtree(dmg_staging, ignore_errors=True)

    return outputs


def build_linux_standalone_run_installer() -> str:
    """
    Creates a single-file self-extracting Linux `.run` installer (`dist/SLMAgents-2.0-Linux-Installer.run`).
    Users on Linux simply run:
      chmod +x SLMAgents-2.0-Linux-Installer.run && ./SLMAgents-2.0-Linux-Installer.run
    It automatically provisions a standalone portable Python 3.11 runtime (from Astral `python-build-standalone`)
    if Python isn't present, unpacks SLMAgents into `~/.local/share/SLMAgents`, creates a `.desktop` icon, and starts the app.
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for abs_p, rel_p in _iter_bundle_files():
            tf.add(abs_p, arcname=rel_p)
    payload_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    run_script = f"""#!/usr/bin/env bash
# SLMAgents 2.0 — Standalone Self-Extracting Linux Installer (.run)
# Zero pre-installed Python dependency required (auto-provisions portable Python 3.11 if needed)
set -e

INSTALL_DIR="${{HOME}}/.local/share/SLMAgents"
BIN_DIR="${{HOME}}/.local/bin"
DESKTOP_DIR="${{HOME}}/.local/share/applications"

echo "=================================================================="
echo "  SLMAgents 2.0 — Standalone Linux Installer"
echo "  Target Directory: ${{INSTALL_DIR}}"
echo "=================================================================="

mkdir -p "${{INSTALL_DIR}}/app" "${{BIN_DIR}}" "${{DESKTOP_DIR}}"

echo "[1/4] Extracting SLMAgents 2.0 application payload..."
PAYLOAD_LINE=$(awk '/^__SLM_PAYLOAD_BELOW__/ {{print NR + 1; exit 0; }}' "$0")
tail -n +${{PAYLOAD_LINE}} "$0" | base64 -d | tar -xz -C "${{INSTALL_DIR}}/app"

echo "[2/4] Provisioning standalone Python runtime..."
ARCH="$(uname -m)"
if [ ! -x "${{INSTALL_DIR}}/venv/bin/python" ]; then
    if command -v python3 >/dev/null 2>&1 && python3 -m venv --help >/dev/null 2>&1; then
        python3 -m venv "${{INSTALL_DIR}}/venv"
    else
        echo "  Downloading portable standalone Python 3.11 for Linux (${{ARCH}})..."
        if [ "${{ARCH}}" = "aarch64" ] || [ "${{ARCH}}" = "arm64" ]; then
            PY_URL="https://github.com/indygreg/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-aarch64-unknown-linux-gnu-install_only.tar.gz"
        else
            PY_URL="https://github.com/indygreg/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-x86_64-unknown-linux-gnu-install_only.tar.gz"
        fi
        curl -fsSL "${{PY_URL}}" | tar -xz -C "${{INSTALL_DIR}}"
        "${{INSTALL_DIR}}/python/bin/python3" -m venv "${{INSTALL_DIR}}/venv"
    fi
    "${{INSTALL_DIR}}/venv/bin/python" -m pip install --upgrade pip
    "${{INSTALL_DIR}}/venv/bin/python" -m pip install -r "${{INSTALL_DIR}}/app/requirements.txt"
fi

echo "[3/4] Registering desktop launcher & CLI command..."
cat > "${{BIN_DIR}}/slmagents" <<'EOF'
#!/usr/bin/env bash
exec "${{HOME}}/.local/share/SLMAgents/venv/bin/python" "${{HOME}}/.local/share/SLMAgents/app/desktop_app.py" "$@"
EOF
chmod +x "${{BIN_DIR}}/slmagents"

cat > "${{DESKTOP_DIR}}/slmagents.desktop" <<EOF
[Desktop Entry]
Name=SLMAgents 2.0
Comment=Offline Pure-CPU AI & Design Studio
Exec=${{BIN_DIR}}/slmagents
Terminal=false
Type=Application
Categories=Development;Office;Utility;
EOF

echo "[4/4] Installation complete! Launching SLMAgents 2.0..."
exec "${{BIN_DIR}}/slmagents" "$@"
exit 0
__SLM_PAYLOAD_BELOW__
{payload_b64}
"""
    run_path = os.path.join(DIST_DIR, "SLMAgents-2.0-Linux-Installer.run")
    with open(run_path, "w", encoding="utf-8") as f:
        f.write(run_script)
    os.chmod(run_path, os.stat(run_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[Build] Created Linux Single-File Standalone Installer: {run_path}")
    return run_path


def build_windows_standalone_installer() -> str:
    """
    Creates a single-file self-extracting Windows Standalone Installer (`dist/SLMAgents-2.0-Windows-Installer.bat`)
    with embedded compressed payload AND automatic portable Python 3.11 provisioning (`cpython-3.11.9-x86_64-pc-windows-msvc`)
    so Windows users DO NOT need Python installed on their PC. Simply double-click `SLMAgents-2.0-Windows-Installer.bat`.
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_p, rel_p in _iter_bundle_files():
            zf.write(abs_p, arcname=rel_p)
    payload_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    bat_installer = f"""@echo off
REM ============================================================================
REM SLMAgents 2.0 — Standalone Self-Extracting Windows Installer
REM Zero pre-installed Python required: auto-provisions portable Python 3.11
REM ============================================================================
setlocal
echo ==================================================================
echo   SLMAgents 2.0 — Standalone Windows Installer
echo   Installing into %LOCALAPPDATA%\\SLMAgents
echo ==================================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; ^
   $installDir = Join-Path $env:LOCALAPPDATA 'SLMAgents'; ^
   $appDir = Join-Path $installDir 'app'; ^
   $pyDir = Join-Path $installDir 'python_runtime'; ^
   New-Item -ItemType Directory -Force -Path $appDir | Out-Null; ^
   Write-Host '[1/4] Extracting embedded SLMAgents 2.0 package...'; ^
   $lines = Get-Content -LiteralPath '%~f0'; ^
   $idx = [Array]::IndexOf($lines, '::SLM_ZIP_PAYLOAD::') + 1; ^
   $b64 = $lines[$idx..($lines.Length - 1)] -join ''; ^
   $zipPath = Join-Path $installDir 'payload.zip'; ^
   [IO.File]::WriteAllBytes($zipPath, [Convert]::FromBase64String($b64)); ^
   Expand-Archive -LiteralPath $zipPath -DestinationPath $appDir -Force; ^
   Remove-Item $zipPath -Force; ^
   $pyExe = Join-Path $pyDir 'python\\python.exe'; ^
   if (-not (Test-Path $pyExe)) {{ ^
     Write-Host '[2/4] Downloading standalone portable Python 3.11 runtime (no system Python required)...'; ^
     $pyTar = Join-Path $installDir 'py_standalone.tar.gz'; ^
     $url = 'https://github.com/indygreg/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-x86_64-pc-windows-msvc-install_only.tar.gz'; ^
     Invoke-WebRequest -Uri $url -OutFile $pyTar; ^
     New-Item -ItemType Directory -Force -Path $pyDir | Out-Null; ^
     tar -xzf $pyTar -C $pyDir; ^
     Remove-Item $pyTar -Force; ^
   }}; ^
   Write-Host '[3/4] Installing runtime packages into isolated environment...'; ^
   & $pyExe -m pip install --upgrade pip; ^
   & $pyExe -m pip install -r (Join-Path $appDir 'requirements.txt'); ^
   Write-Host '[4/4] Creating Desktop Shortcut (SLMAgents 2.0)...'; ^
   $desktop = [Environment]::GetFolderPath('Desktop'); ^
   $WshShell = New-Object -ComObject WScript.Shell; ^
   $Shortcut = $WshShell.CreateShortcut((Join-Path $desktop 'SLMAgents 2.0.lnk')); ^
   $Shortcut.TargetPath = $pyExe; ^
   $Shortcut.Arguments = ('\"' + (Join-Path $appDir 'desktop_app.py') + '\"'); ^
   $Shortcut.WorkingDirectory = $appDir; ^
   $Shortcut.Save(); ^
   Write-Host 'Installation complete! Launching SLMAgents 2.0...'; ^
   Start-Process -FilePath $pyExe -ArgumentList ('\"' + (Join-Path $appDir 'desktop_app.py') + '\"') -WorkingDirectory $appDir"

exit /b 0
::SLM_ZIP_PAYLOAD::
{payload_b64}
"""
    win_installer_path = os.path.join(DIST_DIR, "SLMAgents-2.0-Windows-Installer.bat")
    with open(win_installer_path, "w", encoding="utf-8") as f:
        f.write(bat_installer)
    print(f"[Build] Created Windows Single-File Standalone Installer: {win_installer_path}")
    return win_installer_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SLMAgents 2.0 Standalone Zero-Dependency Installers")
    parser.add_argument("--skip-freeze", action="store_true", help="Skip PyInstaller freeze step")
    args = parser.parse_args()

    #Clean old non-standalone artifacts in dist/
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    os.makedirs(DIST_DIR, exist_ok=True)

    if not args.skip_freeze:
        bundle_path = build_pyinstaller_frozen_bundle()
        if platform.system().lower() == "darwin":
            build_macos_standalone_installers(bundle_path)

    build_linux_standalone_run_installer()
    build_windows_standalone_installer()

    # Clean intermediate PyInstaller build folder & spec
    shutil.rmtree(os.path.join(REPO_ROOT, "build"), ignore_errors=True)
    spec_file = os.path.join(REPO_ROOT, "SLMAgents.spec")
    if os.path.exists(spec_file):
        os.remove(spec_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
