#!/usr/bin/env python3
"""
Standalone Zero-Dependency Installer Builder for SLMAgents 2.0 (macOS, Windows, Linux).

Produces true standalone installers that DO NOT require Python to be pre-installed,
with a bespoke Silicon-Neural-Agent custom icon (`SLMAgents.icns` / `.ico` / `.png`)
and a pure Chat-only UI payload (excluding `index.html`, `blogs.html`, `playground.html`).
"""

from __future__ import annotations

import argparse
import base64
import io
import math
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
import zipfile
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIST_DIR = os.path.join(REPO_ROOT, "dist")
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")


def generate_macos_icns() -> Optional[str]:
    """
    Renders a bespoke, supersampled 2048x2048 -> 1024x1024 SLMAgents 2.0 Installer & App Icon:
      - Deep Obsidian-Indigo macOS squircle with neon cyan/violet rim
      - 32 glowing gold/cyan CPU microprocessor pins around a dark-titanium silicon die
      - 3D Isometric Neural Agent Prism (Cyan, Violet, Emerald) with glowing synaptic nodes at the center
    """
    os.makedirs(ASSETS_DIR, exist_ok=True)
    web_assets = os.path.join(REPO_ROOT, "website", "assets")
    os.makedirs(web_assets, exist_ok=True)

    icns_path = os.path.join(ASSETS_DIR, "SLMAgents.icns")
    png_path = os.path.join(ASSETS_DIR, "SLMAgents.png")
    ico_path = os.path.join(ASSETS_DIR, "SLMAgents.ico")
    web_png_path = os.path.join(web_assets, "slm-installer-icon.png")

    try:
        from PIL import Image, ImageDraw, ImageFilter

        ss = 2048  # 2x supersampling for crisp anti-aliased edges
        img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 1. Outer Squircle Shadow & Base
        margin = 120
        draw.rounded_rectangle(
            [margin, margin, ss - margin, ss - margin],
            radius=410,
            fill=(11, 15, 30, 255),
            outline=(99, 102, 241, 255),
            width=24,
        )

        # Inner glowing frame
        inner_frame = 168
        draw.rounded_rectangle(
            [inner_frame, inner_frame, ss - inner_frame, ss - inner_frame],
            radius=365,
            fill=(22, 27, 58, 255),
            outline=(56, 189, 248, 190),
            width=10,
        )

        # Subtle circuit grid lines across background
        for offset in range(380, ss - 380, 160):
            draw.line([(offset, 220), (offset, ss - 220)], fill=(56, 189, 248, 28), width=4)
            draw.line([(220, offset), (ss - 220, offset)], fill=(167, 139, 250, 28), width=4)

        # 2. Silicon CPU Microprocessor Pins (Top, Bottom, Left, Right)
        chip_margin = 430
        pin_len = 115
        pin_w = 34
        pin_positions = [560, 720, 880, 1024, 1168, 1328, 1488]
        for pos in pin_positions:
            # Top pins
            draw.rounded_rectangle(
                [pos - pin_w // 2, chip_margin - pin_len, pos + pin_w // 2, chip_margin + 20],
                radius=12,
                fill=(56, 189, 248, 255),
            )
            draw.ellipse([pos - 18, chip_margin - pin_len - 18, pos + 18, chip_margin - pin_len + 18], fill=(167, 139, 250, 255))
            # Bottom pins
            draw.rounded_rectangle(
                [pos - pin_w // 2, ss - chip_margin - 20, pos + pin_w // 2, ss - chip_margin + pin_len],
                radius=12,
                fill=(52, 211, 153, 255),
            )
            draw.ellipse([pos - 18, ss - chip_margin + pin_len - 18, pos + 18, ss - chip_margin + pin_len + 18], fill=(56, 189, 248, 255))
            # Left pins
            draw.rounded_rectangle(
                [chip_margin - pin_len, pos - pin_w // 2, chip_margin + 20, pos + pin_w // 2],
                radius=12,
                fill=(167, 139, 250, 255),
            )
            draw.ellipse([chip_margin - pin_len - 18, pos - 18, chip_margin - pin_len + 18, pos + 18], fill=(56, 189, 248, 255))
            # Right pins
            draw.rounded_rectangle(
                [ss - chip_margin - 20, pos - pin_w // 2, ss - chip_margin + pin_len, pos + pin_w // 2],
                radius=12,
                fill=(56, 189, 248, 255),
            )
            draw.ellipse([ss - chip_margin + pin_len - 18, pos - 18, ss - chip_margin + pin_len + 18, pos + 18], fill=(52, 211, 153, 255))

        # 3. Central Silicon Processor Die Body
        draw.rounded_rectangle(
            [chip_margin, chip_margin, ss - chip_margin, ss - chip_margin],
            radius=170,
            fill=(15, 23, 42, 255),
            outline=(129, 140, 248, 255),
            width=18,
        )
        die_inner = chip_margin + 54
        draw.rounded_rectangle(
            [die_inner, die_inner, ss - die_inner, ss - die_inner],
            radius=130,
            fill=(30, 27, 75, 255),
            outline=(56, 189, 248, 120),
            width=6,
        )

        # 4. 3D Isometric Stacked Neural Agent Prism inside the CPU Core
        cx = ss // 2
        layers = [
            (1220, (16, 185, 129, 245), (167, 243, 208, 255)),   # Bottom Emerald Layer (Tools/Verifiers)
            (1015, (124, 58, 237, 245), (221, 214, 254, 255)),   # Middle Violet Layer (Multi-Agent Kernel)
            (810, (14, 165, 233, 250), (224, 242, 254, 255)),    # Top Cyan Layer (Pure-CPU Reasoning)
        ]
        rx, ry, thickness = 340, 165, 58
        for cy, fill_col, rim_col in layers:
            # 3D side extrusions
            left_side = [
                (cx - rx, cy),
                (cx, cy + ry),
                (cx, cy + ry + thickness),
                (cx - rx, cy + thickness),
            ]
            right_side = [
                (cx + rx, cy),
                (cx, cy + ry),
                (cx, cy + ry + thickness),
                (cx + rx, cy + thickness),
            ]
            darker = (max(0, fill_col[0] - 45), max(0, fill_col[1] - 45), max(0, fill_col[2] - 45), 255)
            deep = (max(0, fill_col[0] - 75), max(0, fill_col[1] - 75), max(0, fill_col[2] - 75), 255)
            draw.polygon(left_side, fill=darker)
            draw.polygon(right_side, fill=deep)

            # Top diamond face
            top_face = [
                (cx, cy - ry),
                (cx + rx, cy),
                (cx, cy + ry),
                (cx - rx, cy),
            ]
            draw.polygon(top_face, fill=fill_col, outline=rim_col)
            draw.line([top_face[0], top_face[1], top_face[2], top_face[3], top_face[0]], fill=rim_col, width=10)

        # Central glowing neural core node on top diamond
        node_r = 46
        draw.ellipse([cx - node_r, 810 - node_r, cx + node_r, 810 + node_r], fill=(255, 255, 255, 255), outline=(56, 189, 248, 255), width=10)

        # Downsample to 1024x1024 master icon
        master = img.resize((1024, 1024), Image.Resampling.LANCZOS)
        master.save(png_path, "PNG")
        master.resize((256, 256), Image.Resampling.LANCZOS).save(web_png_path, "PNG")
        master.save(ico_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])

        if platform.system().lower() == "darwin":
            with tempfile.TemporaryDirectory() as tmpdir:
                iconset_dir = os.path.join(tmpdir, "SLMAgents.iconset")
                os.makedirs(iconset_dir, exist_ok=True)
                icon_specs = [
                    (16, "icon_16x16.png"),
                    (32, "icon_16x16@2x.png"),
                    (32, "icon_32x32.png"),
                    (64, "icon_32x32@2x.png"),
                    (128, "icon_128x128.png"),
                    (256, "icon_128x128@2x.png"),
                    (256, "icon_256x256.png"),
                    (512, "icon_256x256@2x.png"),
                    (512, "icon_512x512.png"),
                    (1024, "icon_512x512@2x.png"),
                ]
                for px, filename in icon_specs:
                    resized = master.resize((px, px), Image.Resampling.LANCZOS)
                    resized.save(os.path.join(iconset_dir, filename), "PNG")

                subprocess.run(["iconutil", "-c", "icns", iconset_dir, "-o", icns_path], check=True)
        print(f"[Build] Generated bespoke SLMAgents Neural-CPU Icon: {icns_path}")
        return icns_path
    except Exception as exc:
        print(f"[Build] Icon generation warning: {exc}")
        return icns_path if os.path.isfile(icns_path) else None


def apply_finder_file_icon(target_path: str) -> None:
    """Applies `assets/SLMAgents.png` directly onto a `.dmg`, `.pkg`, or `.app` in macOS Finder via `NSWorkspace`."""
    if platform.system().lower() != "darwin" or not os.path.exists(target_path):
        return
    png_path = os.path.join(ASSETS_DIR, "SLMAgents.png")
    if not os.path.isfile(png_path):
        return
    try:
        from AppKit import NSImage, NSWorkspace  # type: ignore

        ns_img = NSImage.alloc().initWithContentsOfFile_(png_path)
        if ns_img is not None:
            NSWorkspace.sharedWorkspace().setIcon_forFile_options_(ns_img, os.path.abspath(target_path), 0)
    except Exception as exc:
        print(f"[Build] NSWorkspace icon stamp note for {target_path}: {exc}")


# Marketing/web-only pages excluded from the downloaded Desktop Chat App
DESKTOP_EXCLUDED_WEB_FILES = {
    "index.html",
    "blogs.html",
    "playground.html",
    "sitemap.xml",
    "robots.txt",
}


def _is_excluded_desktop_web_file(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    if not norm.startswith("website/"):
        return False
    base = os.path.basename(norm)
    if base in DESKTOP_EXCLUDED_WEB_FILES or base.startswith("blog-"):
        return True
    return False


def _iter_bundle_files():
    """Yields `(abs_path, rel_path)` for all desktop runtime files (excluding web marketing pages, `.venv`, `models`, `dist`)."""
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
            if _is_excluded_desktop_web_file(rel_p):
                continue
            yield abs_p, rel_p


def build_pyinstaller_frozen_bundle() -> str:
    """
    Freezes `desktop_app.py` + `pywebview` native Cocoa window + Python interpreter + chat-only `website/`
    into a standalone native bundle (`dist/SLMAgents.app` on macOS).
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    icns_path = generate_macos_icns()

    with tempfile.TemporaryDirectory() as staging_dir:
        # Stage a pure Chat-only `website/` directory (no index.html, blogs.html, playground.html)
        staged_website = os.path.join(staging_dir, "website")
        os.makedirs(staged_website, exist_ok=True)
        src_website = os.path.join(REPO_ROOT, "website")
        for root, _, files in os.walk(src_website):
            for fn in files:
                abs_p = os.path.join(root, fn)
                rel_w = os.path.relpath(abs_p, REPO_ROOT)
                if _is_excluded_desktop_web_file(rel_w):
                    continue
                dest_p = os.path.join(staging_dir, rel_w)
                os.makedirs(os.path.dirname(dest_p), exist_ok=True)
                shutil.copy2(abs_p, dest_p)

        add_data_args = [
            f"--add-data={staged_website}{os.pathsep}website",
            f"--add-data=assets{os.pathsep}assets",
            f"--add-data=slm_harness{os.pathsep}slm_harness",
            f"--add-data=download_models.py{os.pathsep}.",
            f"--add-data=main.py{os.pathsep}.",
            f"--add-data=requirements.txt{os.pathsep}.",
        ]
        for entry in sorted(os.listdir(REPO_ROOT)):
            full_p = os.path.join(REPO_ROOT, entry)
            if os.path.isdir(full_p) and entry.startswith("slm_") and entry != "slm_harness":
                add_data_args.append(f"--add-data={entry}{os.pathsep}{entry}")

        hidden_imports = [
            "--hidden-import=main",
            "--hidden-import=webview",
            "--hidden-import=webview.platforms.cocoa",
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

        icon_args = [f"--icon={icns_path}"] if icns_path and os.path.isfile(icns_path) else []

        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--name=SLMAgents",
            "--windowed",
            "--osx-bundle-identifier=ai.slmagents.desktop",
            *icon_args,
            *add_data_args,
            *hidden_imports,
            "desktop_app.py",
        ]
        print("[Build] Freezing standalone zero-dependency Chat App with PyInstaller...")
        subprocess.run(cmd, cwd=REPO_ROOT, check=True)

    app_bundle = os.path.join(DIST_DIR, "SLMAgents.app")
    if os.path.exists(app_bundle):
        if platform.system().lower() == "darwin":
            if icns_path and os.path.isfile(icns_path):
                shutil.copy2(icns_path, os.path.join(app_bundle, "Contents", "Resources", "SLMAgents.icns"))
            subprocess.run(["xattr", "-cr", app_bundle], check=False)
            subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app_bundle], check=False)
            apply_finder_file_icon(app_bundle)
        print(f"[Build] Frozen standalone macOS app ready: {app_bundle}")
        return app_bundle
    return os.path.join(DIST_DIR, "SLMAgents")


def build_macos_standalone_installers(app_path: str) -> list[str]:
    """
    Wraps the standalone `dist/SLMAgents.app` into:
      1. `dist/SLMAgents-2.0-macOS-Installer.pkg` (with custom icon + postinstall quarantine removal)
      2. `dist/SLMAgents-2.0-macOS-Installer.dmg` (with custom icon + `SLMAgents.app` + `Applications` symlink)
      3. Installs directly to `/Applications/SLMAgents.app` and refreshes macOS LaunchServices icon cache
    """
    outputs: list[str] = []
    if platform.system().lower() != "darwin" or not os.path.exists(app_path):
        return outputs

    try:
        target_app = "/Applications/SLMAgents.app"
        if os.path.exists(target_app):
            shutil.rmtree(target_app)
        shutil.copytree(app_path, target_app, symlinks=True)
        subprocess.run(["xattr", "-cr", target_app], check=False)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", target_app], check=False)
        apply_finder_file_icon(target_app)
        lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
        if os.path.exists(lsregister):
            subprocess.run([lsregister, "-f", target_app], check=False)
        subprocess.run(["touch", target_app], check=False)
        print(f"[Build] Installed directly to {target_app} with custom icon")
    except Exception as exc:
        print(f"[Build] Direct /Applications install note: {exc}")

    # 1. Native macOS .pkg Wizard Installer
    pkg_path = os.path.join(DIST_DIR, "SLMAgents-2.0-macOS-Installer.pkg")
    if os.path.exists(pkg_path):
        os.remove(pkg_path)
    try:
        with tempfile.TemporaryDirectory() as scripts_dir:
            postinstall = os.path.join(scripts_dir, "postinstall")
            with open(postinstall, "w", encoding="utf-8") as f:
                f.write(
                    "#!/bin/bash\n"
                    "xattr -cr /Applications/SLMAgents.app 2>/dev/null || true\n"
                    "codesign --force --deep --sign - /Applications/SLMAgents.app 2>/dev/null || true\n"
                    "touch /Applications/SLMAgents.app 2>/dev/null || true\n"
                    "open /Applications/SLMAgents.app 2>/dev/null || true\n"
                    "exit 0\n"
                )
            os.chmod(postinstall, 0o755)
            subprocess.run(
                [
                    "pkgbuild",
                    "--component",
                    app_path,
                    "--install-location",
                    "/Applications",
                    "--scripts",
                    scripts_dir,
                    "--identifier",
                    "ai.slmagents.desktop.pkg",
                    "--version",
                    "2.0.0",
                    pkg_path,
                ],
                check=True,
            )
            subprocess.run(["xattr", "-cr", pkg_path], check=False)
            apply_finder_file_icon(pkg_path)
            outputs.append(pkg_path)
            print(f"[Build] Created macOS Standalone .pkg Wizard Installer: {pkg_path}")
    except Exception as exc:
        print(f"[Build] pkgbuild warning: {exc}")

    # 2. Native macOS .dmg Disk Image
    dmg_path = os.path.join(DIST_DIR, "SLMAgents-2.0-macOS-Installer.dmg")
    if os.path.exists(dmg_path):
        os.remove(dmg_path)
    dmg_staging = os.path.join(DIST_DIR, "_dmg_staging")
    if os.path.exists(dmg_staging):
        shutil.rmtree(dmg_staging)
    os.makedirs(dmg_staging, exist_ok=True)
    try:
        staged_app = os.path.join(dmg_staging, "SLMAgents.app")
        shutil.copytree(app_path, staged_app, symlinks=True)
        subprocess.run(["xattr", "-cr", staged_app], check=False)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", staged_app], check=False)
        apply_finder_file_icon(staged_app)
        os.symlink("/Applications", os.path.join(dmg_staging, "Applications"))

        helper_cmd = os.path.join(dmg_staging, "Install & Open SLMAgents.command")
        with open(helper_cmd, "w", encoding="utf-8") as f:
            f.write(
                "#!/usr/bin/env bash\n"
                "set -e\n"
                'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
                'echo "Installing SLMAgents 2.0 to /Applications/SLMAgents.app ..."\n'
                "rm -rf /Applications/SLMAgents.app\n"
                'cp -R "$DIR/SLMAgents.app" /Applications/SLMAgents.app\n'
                "xattr -cr /Applications/SLMAgents.app 2>/dev/null || true\n"
                'echo "Launching SLMAgents 2.0..."\n'
                "open /Applications/SLMAgents.app\n"
            )
        os.chmod(helper_cmd, 0o755)
        apply_finder_file_icon(helper_cmd)

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
        subprocess.run(["xattr", "-cr", dmg_path], check=False)
        apply_finder_file_icon(dmg_path)
        outputs.append(dmg_path)
        print(f"[Build] Created macOS Standalone .dmg Installer: {dmg_path}")
    except Exception as exc:
        print(f"[Build] DMG creation warning: {exc}")
    finally:
        shutil.rmtree(dmg_staging, ignore_errors=True)

    return outputs


def build_linux_standalone_run_installer() -> str:
    """Creates a single-file self-extracting Linux `.run` installer (`dist/SLMAgents-2.0-Linux-Installer.run`)."""
    os.makedirs(DIST_DIR, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for abs_p, rel_p in _iter_bundle_files():
            tf.add(abs_p, arcname=rel_p)
    payload_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    run_script = f"""#!/usr/bin/env bash
# SLMAgents 2.0 — Standalone Self-Extracting Linux Installer (.run)
set -e

INSTALL_DIR="${{HOME}}/.local/share/SLMAgents"
BIN_DIR="${{HOME}}/.local/bin"
DESKTOP_DIR="${{HOME}}/.local/share/applications"

echo "=================================================================="
echo "  SLMAgents 2.0 — Standalone Linux Installer"
echo "  Target Directory: ${{INSTALL_DIR}}"
echo "=================================================================="

mkdir -p "${{INSTALL_DIR}}/app" "${{BIN_DIR}}" "${{DESKTOP_DIR}}"

echo "[1/4] Extracting SLMAgents 2.0 Chat Studio payload..."
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

echo "[3/4] Registering desktop launcher & custom icon..."
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
Icon=${{INSTALL_DIR}}/app/assets/SLMAgents.png
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
    """Creates a single-file self-extracting Windows Standalone Installer (`dist/SLMAgents-2.0-Windows-Installer.bat`)."""
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
   Write-Host '[1/4] Extracting embedded SLMAgents 2.0 Chat Studio...'; ^
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
   Write-Host '[4/4] Creating Desktop Shortcut with SLMAgents Icon...'; ^
   $desktop = [Environment]::GetFolderPath('Desktop'); ^
   $WshShell = New-Object -ComObject WScript.Shell; ^
   $Shortcut = $WshShell.CreateShortcut((Join-Path $desktop 'SLMAgents 2.0.lnk')); ^
   $Shortcut.TargetPath = $pyExe; ^
   $Shortcut.Arguments = ('\"' + (Join-Path $appDir 'desktop_app.py') + '\"'); ^
   $Shortcut.WorkingDirectory = $appDir; ^
   $icoFile = Join-Path $appDir 'assets\\SLMAgents.ico'; ^
   if (Test-Path $icoFile) {{ $Shortcut.IconLocation = $icoFile }}; ^
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

    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    os.makedirs(DIST_DIR, exist_ok=True)

    generate_macos_icns()

    if not args.skip_freeze:
        bundle_path = build_pyinstaller_frozen_bundle()
        if platform.system().lower() == "darwin":
            build_macos_standalone_installers(bundle_path)

    build_linux_standalone_run_installer()
    build_windows_standalone_installer()

    shutil.rmtree(os.path.join(REPO_ROOT, "build"), ignore_errors=True)
    spec_file = os.path.join(REPO_ROOT, "SLMAgents.spec")
    if os.path.exists(spec_file):
        os.remove(spec_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
