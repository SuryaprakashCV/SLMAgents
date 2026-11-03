import os
import sys
import shutil
import subprocess
from huggingface_hub import HfApi, get_token


def resolve_hf_token() -> str:
    token = os.environ.get("HF_TOKEN") or get_token()
    if token:
        return token.strip()
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=huggingface.co\n\n",
            text=True,
            capture_output=True,
            timeout=5,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def main():
    print("==========================================================")
    print("  Deploying SLMAgents 2.0 to Hugging Face Space (spcv/slm-agents)")
    print("==========================================================")

    token = resolve_hf_token()
    if not token and sys.stdin.isatty():
        token = input("Enter your Hugging Face Access Token: ").strip()
    if not token:
        print("Error: Hugging Face token not found.")
        sys.exit(1)

    api = HfApi(token=token)
    username = "spcv"
    space_repo_id = f"{username}/SLM-Agents"

    temp_dir = ".hf_deploy_temp"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    print("\n[*] Packing SLMAgents 2.0 codebase (10-Plane Harness + 26 Agents + Website)...")
    ignore_pat = shutil.ignore_patterns(
        ".venv", ".git", ".vscode", "__pycache__", "dist", "build",
        "*.egg-info", "*.pyc", ".DS_Store", "*.zip", "*.onnx", "*.data",
        "*.bin", "*.safetensors", "*.pth", "*.gguf", "test*", "scratch*", "models*",
        "*.log", "*.workspace", "predictions*", "predict_*.json", "speech.m4a"
    )

    # Copy slm_harness and all 26 slm_* agent packages
    for item in sorted(os.listdir(".")):
        if os.path.isdir(item) and item.startswith("slm_") and item != ".hf_deploy_temp":
            shutil.copytree(item, os.path.join(temp_dir, item), ignore=ignore_pat)

    # Copy website, scripts, assets, and .slm skills repository
    for folder in ["website", "scripts", "assets", ".slm"]:
        if os.path.isdir(folder):
            shutil.copytree(folder, os.path.join(temp_dir, folder), ignore=ignore_pat)

    # Copy pre-built lightweight Windows (.exe) and Linux (.AppImage) installers if present
    os.makedirs(os.path.join(temp_dir, "dist"), exist_ok=True)
    for installer_file in [
        "SLMAgents-2.0-Windows-Setup.exe",
        "SLMAgents-2.0-Linux-x86_64.AppImage",
    ]:
        src_inst = os.path.join("dist", installer_file)
        if os.path.isfile(src_inst):
            shutil.copy(src_inst, os.path.join(temp_dir, "dist", installer_file))

    # Copy root application & runtime files
    for config_file in [
        "Dockerfile",
        "requirements.txt",
        ".dockerignore",
        "main.py",
        "slm_batch_engine.py",
        "desktop_app.py",
        "slm",
        "SLMAgents.command",
        "SLMAgents.bat",
        "README.md",
        "config.xml",
        "download_models.py",
    ]:
        if os.path.exists(config_file):
            shutil.copy(config_file, os.path.join(temp_dir, config_file))

    total_size = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, dn, filenames in os.walk(temp_dir)
        for f in filenames
    )
    print(f"[*] Total package size: {round(total_size / (1024 * 1024), 2)} MB")

    print(f"\n[*] Verifying Hugging Face Space: {space_repo_id}...")
    try:
        try:
            api.space_info(repo_id=space_repo_id)
        except Exception:
            api.create_repo(
                repo_id=space_repo_id,
                repo_type="space",
                space_sdk="docker",
                exist_ok=True,
            )

        print(f"[*] Uploading SLMAgents 2.0 to {space_repo_id}...")
        commit_info = api.upload_folder(
            folder_path=temp_dir,
            repo_id=space_repo_id,
            repo_type="space",
            commit_message="feat(v2.0): deploy SLMAgents 2.0 10-Plane Pure-CPU Harness, redesigned website, and OS installers",
        )
        print("\n==========================================================")
        print("Successfully deployed SLMAgents 2.0 to Hugging Face Space!")
        print(f"Commit URL: {getattr(commit_info, 'commit_url', 'https://huggingface.co/spaces/' + space_repo_id)}")
        print(f"Space URL:  https://huggingface.co/spaces/{space_repo_id}")
        print("==========================================================")
    except Exception as e:
        print(f"Error during Space upload: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    main()
