#!/usr/bin/env python3
"""
Standalone Zero-Dependency Installer Builder for SLMAgents 2.0:
  - macOS:   `dist/SLMAgents-2.0-macOS-Installer.dmg` (.dmg Disk Image with frozen SLMAgents.app)
  - Windows: `dist/SLMAgents-2.0-Windows-Setup.exe` (.exe PE32+ 64-bit Windows Self-Extracting Setup Executable)
  - Linux:   `dist/SLMAgents-2.0-Linux-x86_64.AppImage` (.AppImage Self-Contained Linux Executable Bundle)
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import platform
import shutil
import stat
import struct
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
      - 28 glowing cyan/emerald/violet CPU microprocessor pins around a dark-titanium silicon die
      - 3D Isometric Neural Agent Prism (Cyan, Violet, Emerald) at the center
    """
    os.makedirs(ASSETS_DIR, exist_ok=True)
    web_assets = os.path.join(REPO_ROOT, "website", "assets")
    os.makedirs(web_assets, exist_ok=True)

    icns_path = os.path.join(ASSETS_DIR, "SLMAgents.icns")
    png_path = os.path.join(ASSETS_DIR, "SLMAgents.png")
    ico_path = os.path.join(ASSETS_DIR, "SLMAgents.ico")
    web_png_path = os.path.join(web_assets, "slm-installer-icon.png")

    try:
        from PIL import Image, ImageDraw

        ss = 2048
        img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        margin = 120
        draw.rounded_rectangle(
            [margin, margin, ss - margin, ss - margin],
            radius=410,
            fill=(11, 15, 30, 255),
            outline=(99, 102, 241, 255),
            width=24,
        )

        inner_frame = 168
        draw.rounded_rectangle(
            [inner_frame, inner_frame, ss - inner_frame, ss - inner_frame],
            radius=365,
            fill=(22, 27, 58, 255),
            outline=(56, 189, 248, 190),
            width=10,
        )

        for offset in range(380, ss - 380, 160):
            draw.line([(offset, 220), (offset, ss - 220)], fill=(56, 189, 248, 28), width=4)
            draw.line([(220, offset), (ss - 220, offset)], fill=(167, 139, 250, 28), width=4)

        chip_margin = 430
        pin_len = 115
        pin_w = 34
        pin_positions = [560, 720, 880, 1024, 1168, 1328, 1488]
        for pos in pin_positions:
            draw.rounded_rectangle([pos - pin_w // 2, chip_margin - pin_len, pos + pin_w // 2, chip_margin + 20], radius=12, fill=(56, 189, 248, 255))
            draw.ellipse([pos - 18, chip_margin - pin_len - 18, pos + 18, chip_margin - pin_len + 18], fill=(167, 139, 250, 255))
            draw.rounded_rectangle([pos - pin_w // 2, ss - chip_margin - 20, pos + pin_w // 2, ss - chip_margin + pin_len], radius=12, fill=(52, 211, 153, 255))
            draw.ellipse([pos - 18, ss - chip_margin + pin_len - 18, pos + 18, ss - chip_margin + pin_len + 18], fill=(56, 189, 248, 255))
            draw.rounded_rectangle([chip_margin - pin_len, pos - pin_w // 2, chip_margin + 20, pos + pin_w // 2], radius=12, fill=(167, 139, 250, 255))
            draw.ellipse([chip_margin - pin_len - 18, pos - 18, chip_margin - pin_len + 18, pos + 18], fill=(56, 189, 248, 255))
            draw.rounded_rectangle([ss - chip_margin - 20, pos - pin_w // 2, ss - chip_margin + pin_len, pos + pin_w // 2], radius=12, fill=(56, 189, 248, 255))
            draw.ellipse([ss - chip_margin + pin_len - 18, pos - 18, ss - chip_margin + pin_len + 18, pos + 18], fill=(52, 211, 153, 255))

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

        cx = ss // 2
        layers = [
            (1220, (16, 185, 129, 245), (167, 243, 208, 255)),
            (1015, (124, 58, 237, 245), (221, 214, 254, 255)),
            (810, (14, 165, 233, 250), (224, 242, 254, 255)),
        ]
        rx, ry, thickness = 340, 165, 58
        for cy, fill_col, rim_col in layers:
            left_side = [(cx - rx, cy), (cx, cy + ry), (cx, cy + ry + thickness), (cx - rx, cy + thickness)]
            right_side = [(cx + rx, cy), (cx, cy + ry), (cx, cy + ry + thickness), (cx + rx, cy + thickness)]
            darker = (max(0, fill_col[0] - 45), max(0, fill_col[1] - 45), max(0, fill_col[2] - 45), 255)
            deep = (max(0, fill_col[0] - 75), max(0, fill_col[1] - 75), max(0, fill_col[2] - 75), 255)
            draw.polygon(left_side, fill=darker)
            draw.polygon(right_side, fill=deep)
            top_face = [(cx, cy - ry), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy)]
            draw.polygon(top_face, fill=fill_col, outline=rim_col)
            draw.line([top_face[0], top_face[1], top_face[2], top_face[3], top_face[0]], fill=rim_col, width=10)

        node_r = 46
        draw.ellipse([cx - node_r, 810 - node_r, cx + node_r, 810 + node_r], fill=(255, 255, 255, 255), outline=(56, 189, 248, 255), width=10)

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
    """Applies `assets/SLMAgents.png` directly onto a `.dmg` or `.app` in macOS Finder via `NSWorkspace`."""
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
    """Freezes `desktop_app.py` + `pywebview` native Cocoa window + Python interpreter + chat-only `website/`."""
    os.makedirs(DIST_DIR, exist_ok=True)
    icns_path = generate_macos_icns()

    with tempfile.TemporaryDirectory() as staging_dir:
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
                if fn == "chat.html":
                    with open(dest_p, "r", encoding="utf-8") as cf:
                        html_txt = cf.read()
                    html_txt = html_txt.replace('<html lang="en">', '<html lang="en" class="desktop-app-mode">')
                    with open(dest_p, "w", encoding="utf-8") as cf:
                        cf.write(html_txt)

        add_data_args = [
            f"--add-data={staged_website}{os.pathsep}website",
            f"--add-data=assets{os.pathsep}assets",
            f"--add-data=slm_harness{os.pathsep}slm_harness",
            f"--add-data=download_models.py{os.pathsep}.",
            f"--add-data=main.py{os.pathsep}.",
            f"--add-data=requirements.txt{os.pathsep}.",
        ]
        if os.path.isdir(os.path.join(REPO_ROOT, ".slm", "skills")):
            add_data_args.append(f"--add-data=.slm/skills{os.pathsep}.slm/skills")
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
            "--hidden-import=yt_dlp",
            "--hidden-import=youtube_transcript_api",
            "--hidden-import=rapidocr_onnxruntime",
            "--hidden-import=cv2",
            "--hidden-import=fitz",
            "--hidden-import=slm_harness.tools.personal_concierge",
            "--hidden-import=slm_harness.tools.media_doc_intelligence",
            "--hidden-import=slm_harness.tools.machine_control",
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
    Wraps the standalone `dist/SLMAgents.app` into `dist/SLMAgents-2.0-macOS-Installer.dmg`
    and installs directly to `/Applications/SLMAgents.app`.
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

    # Remove legacy .pkg if present so macOS strictly uses .dmg
    legacy_pkg = os.path.join(DIST_DIR, "SLMAgents-2.0-macOS-Installer.pkg")
    if os.path.exists(legacy_pkg):
        os.remove(legacy_pkg)

    # Native macOS .dmg Disk Image
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


def _build_pe32plus_win64_sfx_stub(cmdline_bytes: bytes) -> bytes:
    """
    Constructs a valid 64-bit Windows PE32+ executable (`x86_64`) that:
      1. Calls `GetModuleFileNameA(NULL, buf, 512)` to locate its own `.exe` file path
      2. Calls `SetEnvironmentVariableA("SLM_SETUP_EXE", buf)`
      3. Calls `WinExec(cmdline, SW_SHOWNORMAL)` to extract the appended ZIP archive & launch SLMAgents 2.0
      4. Calls `ExitProcess(0)`
    """
    # Layout:
    # File 0x000..0x1FF: DOS Header + PE32+ Headers (512 bytes)
    # File 0x200..0x3FF: .text section @ RVA 0x1000 (512 bytes)
    # File 0x400..0xBFF: .rdata/.idata section @ RVA 0x2000 (2048 bytes)

    # .rdata/.idata layout (@ RVA 0x2000):
    # 0x2000: Import Directory Table (2 entries: KERNEL32.dll + null terminator = 40 bytes)
    # 0x2030: ILT (5 qwords: GetModuleFileNameA, SetEnvironmentVariableA, WinExec, ExitProcess, 0)
    # 0x2060: IAT (5 qwords: GetModuleFileNameA, SetEnvironmentVariableA, WinExec, ExitProcess, 0)
    # 0x2090: "KERNEL32.dll\0"
    # 0x20A0: Hint/Name for GetModuleFileNameA
    # 0x20C0: Hint/Name for SetEnvironmentVariableA
    # 0x20E0: Hint/Name for WinExec
    # 0x20F0: Hint/Name for ExitProcess
    # 0x2110: "SLM_SETUP_EXE\0"
    # 0x2130: cmdline_bytes + "\0"

    rdata = bytearray(2048)

    def _put_bytes(offset: int, b: bytes) -> None:
        rdata[offset : offset + len(b)] = b

    # IDT entry for KERNEL32.dll
    struct.pack_into("<IIIII", rdata, 0x00, 0x2030, 0, 0, 0x2090, 0x2060)
    # ILT & IAT entries
    hint_rvas = (0x20A0, 0x20C0, 0x20E0, 0x20F0, 0)
    for idx, rva in enumerate(hint_rvas):
        struct.pack_into("<Q", rdata, 0x30 + idx * 8, rva)
        struct.pack_into("<Q", rdata, 0x60 + idx * 8, rva)

    _put_bytes(0x90, b"KERNEL32.dll\x00")
    _put_bytes(0xA0, b"\x00\x00GetModuleFileNameA\x00")
    _put_bytes(0xC0, b"\x00\x00SetEnvironmentVariableA\x00")
    _put_bytes(0xE0, b"\x00\x00WinExec\x00")
    _put_bytes(0xF0, b"\x00\x00ExitProcess\x00")
    _put_bytes(0x110, b"SLM_SETUP_EXE\x00")
    _put_bytes(0x130, cmdline_bytes[:1600] + b"\x00")

    # Assemble x86-64 .text (@ RVA 0x1000)
    text = bytearray(512)
    code = bytearray()

    # sub rsp, 0x228
    code += b"\x48\x81\xec\x28\x02\x00\x00"
    # xor ecx, ecx
    code += b"\x31\xc9"
    # lea rdx, [rsp+0x20]
    code += b"\x48\x8d\x54\x24\x20"
    # mov r8d, 0x200
    code += b"\x41\xb8\x00\x02\x00\x00"
    # call [rip + disp32] -> IAT[0] (0x2060)
    next_rip = 0x1000 + len(code) + 6
    code += b"\xff\x15" + struct.pack("<i", 0x2060 - next_rip)

    # lea rcx, [rip + disp32] -> "SLM_SETUP_EXE" (0x2110)
    next_rip = 0x1000 + len(code) + 7
    code += b"\x48\x8d\x0d" + struct.pack("<i", 0x2110 - next_rip)
    # lea rdx, [rsp+0x20]
    code += b"\x48\x8d\x54\x24\x20"
    # call [rip + disp32] -> IAT[1] (0x2068)
    next_rip = 0x1000 + len(code) + 6
    code += b"\xff\x15" + struct.pack("<i", 0x2068 - next_rip)

    # lea rcx, [rip + disp32] -> cmdline (0x2130)
    next_rip = 0x1000 + len(code) + 7
    code += b"\x48\x8d\x0d" + struct.pack("<i", 0x2130 - next_rip)
    # mov edx, 1 (SW_SHOWNORMAL)
    code += b"\xba\x01\x00\x00\x00"
    # call [rip + disp32] -> IAT[2] (0x2070 WinExec)
    next_rip = 0x1000 + len(code) + 6
    code += b"\xff\x15" + struct.pack("<i", 0x2070 - next_rip)

    # xor ecx, ecx
    code += b"\x31\xc9"
    # call [rip + disp32] -> IAT[3] (0x2078 ExitProcess)
    next_rip = 0x1000 + len(code) + 6
    code += b"\xff\x15" + struct.pack("<i", 0x2078 - next_rip)

    text[: len(code)] = code

    # Build DOS + PE32+ Header (512 bytes)
    hdr = bytearray(512)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, 0x40)  # e_lfanew = 0x40

    pe = 0x40
    hdr[pe : pe + 4] = b"PE\x00\x00"
    # COFF File Header (20 bytes)
    struct.pack_into(
        "<HHIIIHH",
        hdr,
        pe + 4,
        0x8664,  # Machine = IMAGE_FILE_MACHINE_AMD64
        2,       # NumberOfSections = 2
        0,
        0,
        0,
        0xF0,    # SizeOfOptionalHeader = 240 (PE32+)
        0x0022,  # Characteristics = EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE
    )

    opt = pe + 24
    struct.pack_into(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        hdr,
        opt,
        0x020B,          # Magic = PE32+ (64-bit)
        14,              # MajorLinkerVersion
        0,               # MinorLinkerVersion
        0x200,           # SizeOfCode
        0x800,           # SizeOfInitializedData
        0,               # SizeOfUninitializedData
        0x1000,          # AddressOfEntryPoint
        0x1000,          # BaseOfCode
        0x140000000,     # ImageBase
        0x1000,          # SectionAlignment
        0x200,           # FileAlignment
        6,               # MajorOperatingSystemVersion
        0,               # MinorOperatingSystemVersion
        0,               # MajorImageVersion
        0,               # MinorImageVersion
        6,               # MajorSubsystemVersion
        0,               # MinorSubsystemVersion
        0,               # Win32VersionValue
        0x3000,          # SizeOfImage
        0x200,           # SizeOfHeaders
        0,               # CheckSum
        3,               # Subsystem = IMAGE_SUBSYSTEM_WINDOWS_CUI
        0x8140,          # DllCharacteristics = DYNAMIC_BASE | NX_COMPAT | TERMINAL_SERVER_AWARE
        0x100000,        # SizeOfStackReserve
        0x1000,          # SizeOfStackCommit
        0x100000,        # SizeOfHeapReserve
        0x1000,          # SizeOfHeapCommit
        0,               # LoaderFlags
        16,              # NumberOfRvaAndSizes
    )
    # Data Directories start at opt + 112
    # Entry 1: Import Directory (RVA 0x2000, Size 40)
    struct.pack_into("<II", hdr, opt + 112 + 8, 0x2000, 40)
    # Entry 12: IAT Directory (RVA 0x2060, Size 40)
    struct.pack_into("<II", hdr, opt + 112 + 12 * 8, 0x2060, 40)

    # Section Headers start at opt + 240
    sec_table = opt + 240
    # Section 0: .text
    struct.pack_into(
        "<8sIIIIIIHHI",
        hdr,
        sec_table,
        b".text\x00\x00\x00",
        0x200,       # VirtualSize
        0x1000,      # VirtualAddress
        0x200,       # SizeOfRawData
        0x200,       # PointerToRawData
        0,
        0,
        0,
        0,
        0x60000020,  # CODE | EXECUTE | READ
    )
    # Section 1: .rdata
    struct.pack_into(
        "<8sIIIIIIHHI",
        hdr,
        sec_table + 40,
        b".rdata\x00\x00",
        0x800,       # VirtualSize
        0x2000,      # VirtualAddress
        0x800,       # SizeOfRawData
        0x400,       # PointerToRawData
        0,
        0,
        0,
        0,
        0xC0000040,  # INITIALIZED_DATA | READ | WRITE
    )

    return bytes(hdr) + bytes(text) + bytes(rdata)


def build_windows_standalone_installer() -> str:
    """
    Builds a native 64-bit Windows PE32+ Self-Extracting `.exe` Installer:
      `dist/SLMAgents-2.0-Windows-Setup.exe`
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    legacy_bat = os.path.join(DIST_DIR, "SLMAgents-2.0-Windows-Installer.bat")
    if os.path.exists(legacy_bat):
        os.remove(legacy_bat)

    # Create the PowerShell bootstrap script inside the ZIP payload so the PE32+ stub command stays compact
    ps_bootstrap = r"""$ErrorActionPreference = 'Stop'
Write-Host '==================================================================' -ForegroundColor Cyan
Write-Host '  SLMAgents 2.0 — Standalone Windows Setup (.exe)' -ForegroundColor Cyan
Write-Host '==================================================================' -ForegroundColor Cyan
$installDir = Join-Path $env:LOCALAPPDATA 'SLMAgents'
$appDir = Join-Path $installDir 'app'
$pyDir = Join-Path $installDir 'python_runtime'
$pyExe = Join-Path $pyDir 'python\python.exe'
if (-not (Test-Path $pyExe)) {
    Write-Host '[1/3] Provisioning standalone portable Python 3.11 runtime (no system Python needed)...'
    $pyTar = Join-Path $installDir 'py_standalone.tar.gz'
    $url = 'https://github.com/indygreg/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-x86_64-pc-windows-msvc-install_only.tar.gz'
    Invoke-WebRequest -Uri $url -OutFile $pyTar
    New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
    tar -xzf $pyTar -C $pyDir
    Remove-Item $pyTar -Force
}
Write-Host '[2/3] Installing Pure-CPU AI & Design Studio dependencies...'
& $pyExe -m pip install --upgrade pip
& $pyExe -m pip install -r (Join-Path $appDir 'requirements.txt')
Write-Host '[3/3] Creating SLMAgents 2.0 Desktop Shortcut...'
$desktop = [Environment]::GetFolderPath('Desktop')
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut((Join-Path $desktop 'SLMAgents 2.0.lnk'))
$Shortcut.TargetPath = $pyExe
$Shortcut.Arguments = ('"' + (Join-Path $appDir 'desktop_app.py') + '"')
$Shortcut.WorkingDirectory = $appDir
$icoFile = Join-Path $appDir 'assets\SLMAgents.ico'
if (Test-Path $icoFile) { $Shortcut.IconLocation = $icoFile }
$Shortcut.Save()
Write-Host 'Installation complete! Launching SLMAgents 2.0...' -ForegroundColor Green
Start-Process -FilePath $pyExe -ArgumentList ('"' + (Join-Path $appDir 'desktop_app.py') + '"') -WorkingDirectory $appDir
"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_p, rel_p in _iter_bundle_files():
            zf.write(abs_p, arcname=rel_p)
        zf.writestr("_win_setup.ps1", ps_bootstrap)
    zip_bytes = buf.getvalue()

    # The PE32+ stub is exactly 3072 bytes (0xC00); the ZIP archive starts at byte offset 3072
    ps_inline = (
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \""
        "$d=Join-Path $env:LOCALAPPDATA 'SLMAgents'; $a=Join-Path $d 'app'; "
        "New-Item -ItemType Directory -Force -Path $a|Out-Null; "
        "$b=[IO.File]::ReadAllBytes($env:SLM_SETUP_EXE); "
        "$z=Join-Path $d 'pkg.zip'; "
        "[IO.File]::WriteAllBytes($z,$b[3072..($b.Length-1)]); "
        "Expand-Archive -LiteralPath $z -DestinationPath $a -Force; "
        "Remove-Item $z -Force; "
        "& (Join-Path $a '_win_setup.ps1')\""
    ).encode("ascii")

    pe_stub = _build_pe32plus_win64_sfx_stub(ps_inline)
    assert len(pe_stub) == 3072, f"Expected 3072-byte PE32+ stub, got {len(pe_stub)}"

    exe_path = os.path.join(DIST_DIR, "SLMAgents-2.0-Windows-Setup.exe")
    with open(exe_path, "wb") as f:
        f.write(pe_stub)
        f.write(zip_bytes)
    print(f"[Build] Created Windows 64-bit PE32+ Standalone Installer: {exe_path}")
    return exe_path


def build_linux_standalone_appimage() -> str:
    """
    Creates a single-file self-contained Linux `.AppImage` executable (`dist/SLMAgents-2.0-Linux-x86_64.AppImage`).
    """
    os.makedirs(DIST_DIR, exist_ok=True)
    legacy_run = os.path.join(DIST_DIR, "SLMAgents-2.0-Linux-Installer.run")
    if os.path.exists(legacy_run):
        os.remove(legacy_run)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for abs_p, rel_p in _iter_bundle_files():
            tf.add(abs_p, arcname=rel_p)
    payload_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    appimage_script = f"""#!/usr/bin/env bash
# AppImage-Compatible Self-Mounting Standalone Linux Bundle for SLMAgents 2.0
set -e

INSTALL_DIR="${{HOME}}/.local/share/SLMAgents"
BIN_DIR="${{HOME}}/.local/bin"
DESKTOP_DIR="${{HOME}}/.local/share/applications"

echo "=================================================================="
echo "  SLMAgents 2.0 — Standalone Linux AppImage"
echo "=================================================================="

mkdir -p "${{INSTALL_DIR}}/app" "${{BIN_DIR}}" "${{DESKTOP_DIR}}"

PAYLOAD_LINE=$(awk '/^__SLM_APPIMAGE_PAYLOAD__/ {{print NR + 1; exit 0; }}' "$0")
tail -n +${{PAYLOAD_LINE}} "$0" | base64 -d | tar -xz -C "${{INSTALL_DIR}}/app"

ARCH="$(uname -m)"
if [ ! -x "${{INSTALL_DIR}}/venv/bin/python" ]; then
    echo "[SLMAgents AppImage] First-run setup: provisioning standalone Python 3.11 runtime..."
    if command -v python3 >/dev/null 2>&1 && python3 -m venv --help >/dev/null 2>&1; then
        python3 -m venv "${{INSTALL_DIR}}/venv"
    else
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

exec "${{BIN_DIR}}/slmagents" "$@"
exit 0
__SLM_APPIMAGE_PAYLOAD__
{payload_b64}
"""
    appimage_path = os.path.join(DIST_DIR, "SLMAgents-2.0-Linux-x86_64.AppImage")
    with open(appimage_path, "w", encoding="utf-8") as f:
        f.write(appimage_script)
    os.chmod(appimage_path, os.stat(appimage_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[Build] Created Linux Standalone .AppImage: {appimage_path}")
    return appimage_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SLMAgents 2.0 Standalone Zero-Dependency Installers (.dmg, .exe, .AppImage)")
    parser.add_argument("--skip-freeze", action="store_true", help="Skip PyInstaller freeze step")
    args = parser.parse_args()

    os.makedirs(DIST_DIR, exist_ok=True)
    generate_macos_icns()

    if not args.skip_freeze:
        if os.path.exists(DIST_DIR):
            shutil.rmtree(DIST_DIR)
        os.makedirs(DIST_DIR, exist_ok=True)
        bundle_path = build_pyinstaller_frozen_bundle()
        if platform.system().lower() == "darwin":
            build_macos_standalone_installers(bundle_path)
    elif platform.system().lower() == "darwin" and os.path.exists(os.path.join(DIST_DIR, "SLMAgents.app")):
        build_macos_standalone_installers(os.path.join(DIST_DIR, "SLMAgents.app"))

    build_windows_standalone_installer()
    build_linux_standalone_appimage()

    shutil.rmtree(os.path.join(REPO_ROOT, "build"), ignore_errors=True)
    spec_file = os.path.join(REPO_ROOT, "SLMAgents.spec")
    if os.path.exists(spec_file):
        os.remove(spec_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
