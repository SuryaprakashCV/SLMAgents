"""
Central Repository Over-The-Air (OTA) Auto-Updater for SLMAgents 2.0 Desktop & CLI.

Allows standalone installed Desktop Apps (macOS `.app`, Windows, Linux) to pull code,
UI (`chat.html`/`app.js`), and `.slm/skills/` updates directly from the central GitHub
repository (`SuryaprakashCV/SLMAgents`, branch `slm-agents-2.0`) using pure Python stdlib
(`urllib.request` + `tarfile`) — zero `git` or Python installation required on client machines.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_CENTRAL_REPO = os.environ.get("SLM_CENTRAL_REPO", "SuryaprakashCV/SLMAgents")
DEFAULT_CENTRAL_BRANCH = os.environ.get("SLM_CENTRAL_BRANCH", "slm-agents-2.0")
SYNC_ROOT = os.path.expanduser("~/.slm_harness/central_sync")
STATE_FILE = os.path.join(SYNC_ROOT, ".sync_state.json")


def get_local_git_sha(repo_dir: str) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        return out if len(out) >= 7 else None
    except Exception:
        return None


def read_sync_state(repo_dir: Optional[str] = None) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "central_repo": DEFAULT_CENTRAL_REPO,
        "branch": DEFAULT_CENTRAL_BRANCH,
        "local_sha": None,
        "last_checked": None,
        "last_updated": None,
    }
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    state.update(saved)
        except Exception:
            pass
    if not state.get("local_sha") and repo_dir:
        state["local_sha"] = get_local_git_sha(repo_dir)
    return state


def _write_sync_state(state: Dict[str, Any]) -> None:
    os.makedirs(SYNC_ROOT, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def fetch_remote_commit_info(
    repo: str = DEFAULT_CENTRAL_REPO,
    branch: str = DEFAULT_CENTRAL_BRANCH,
    timeout: float = 3.5,
) -> Optional[Dict[str, Any]]:
    """Queries GitHub API for the latest commit SHA and message on the central repo branch."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "SLMAgents-2.0-OTA-Updater",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            sha = data.get("sha", "")
            commit_obj = data.get("commit", {}) or {}
            return {
                "sha": sha,
                "short_sha": sha[:7] if sha else "",
                "message": (commit_obj.get("message") or "").splitlines()[0],
                "date": (commit_obj.get("committer") or {}).get("date", ""),
                "author": (commit_obj.get("author") or {}).get("name", ""),
            }
    except Exception:
        return None


def check_for_updates(repo_dir: Optional[str] = None, timeout: float = 3.5) -> Dict[str, Any]:
    state = read_sync_state(repo_dir)
    repo = state.get("central_repo") or DEFAULT_CENTRAL_REPO
    branch = state.get("branch") or DEFAULT_CENTRAL_BRANCH

    remote = fetch_remote_commit_info(repo=repo, branch=branch, timeout=timeout)
    local_sha = state.get("local_sha") or (get_local_git_sha(repo_dir) if repo_dir else None)

    if remote is None:
        return {
            "status": "offline",
            "update_available": False,
            "central_repo": repo,
            "branch": branch,
            "local_sha": local_sha[:7] if local_sha else "bundled",
            "remote_sha": None,
            "message": "Central repository unreachable (offline mode active).",
        }

    remote_sha = remote["sha"]
    update_available = bool(remote_sha and local_sha != remote_sha)
    return {
        "status": "ok",
        "update_available": update_available,
        "central_repo": repo,
        "branch": branch,
        "local_sha": local_sha[:7] if local_sha else "bundled",
        "remote_sha": remote["short_sha"],
        "full_remote_sha": remote_sha,
        "latest_commit_message": remote["message"],
        "latest_commit_date": remote["date"],
    }


def pull_central_update(
    repo_dir: Optional[str] = None,
    force: bool = False,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """
    Downloads the latest tarball from `https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}`,
    extracts updated code, skills (`.slm/skills/`), and UI into `~/.slm_harness/central_sync/`,
    syncs new skills into workspace `.slm/skills/`, and hot-reloads active Python modules.
    """
    status = check_for_updates(repo_dir=repo_dir, timeout=5.0)
    if status["status"] == "offline":
        return status

    if not status["update_available"] and not force and os.path.isdir(os.path.join(SYNC_ROOT, "slm_harness")):
        return {
            **status,
            "updated": False,
            "message": f"Already up to date with {status['central_repo']}@{status['remote_sha']}.",
        }

    repo = status["central_repo"]
    branch = status["branch"]
    tar_url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"

    req = urllib.request.Request(tar_url, headers={"User-Agent": "SLMAgents-2.0-OTA-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw_bytes = resp.read()

    os.makedirs(SYNC_ROOT, exist_ok=True)
    updated_files = 0

    with tarfile.open(fileobj=io.BytesIO(raw_bytes), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue
            rel_path = parts[1]
            # Security guard against path traversal
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                continue
            dest_path = os.path.join(SYNC_ROOT, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is not None:
                with open(dest_path, "wb") as out_f:
                    shutil.copyfileobj(extracted, out_f)
                updated_files += 1

    # Ensure Desktop mode class is preserved in synced chat.html for frozen apps
    synced_skills_dir = os.path.join(SYNC_ROOT, ".slm", "skills")
    if repo_dir and os.path.isdir(synced_skills_dir):
        ws_skills_dir = os.path.join(repo_dir, ".slm", "skills")
        os.makedirs(ws_skills_dir, exist_ok=True)
        for fn in os.listdir(synced_skills_dir):
            if fn.endswith(".md"):
                src_md = os.path.join(synced_skills_dir, fn)
                dst_md = os.path.join(ws_skills_dir, fn)
                if not os.path.exists(dst_md) or os.path.getmtime(src_md) >= os.path.getmtime(dst_md):
                    shutil.copy2(src_md, dst_md)

    # Prepend SYNC_ROOT to sys.path so hot-reloaded modules resolve from central_sync
    if SYNC_ROOT not in sys.path:
        sys.path.insert(0, SYNC_ROOT)

    # Hot-reload skill registry if loaded
    try:
        from .skills.skill_registry import get_skill_registry

        get_skill_registry().reload()
    except Exception:
        pass

    new_state = {
        "central_repo": repo,
        "branch": branch,
        "local_sha": status.get("full_remote_sha"),
        "last_checked": int(time.time()),
        "last_updated": int(time.time()),
        "updated_files": updated_files,
    }
    _write_sync_state(new_state)

    return {
        **status,
        "updated": True,
        "local_sha": status.get("remote_sha"),
        "updated_files": updated_files,
        "sync_dir": SYNC_ROOT,
        "message": f"Successfully synced {updated_files} files from {repo}@{status.get('remote_sha')}.",
    }


def activate_central_sync_overlay() -> Optional[str]:
    """
    Called at Desktop App startup (`desktop_app.py`).
    Checks if a newer commit is on the central GitHub repo, pulls it into `~/.slm_harness/central_sync/`,
    and activates `~/.slm_harness/central_sync/` as the primary code & UI root.
    """
    try:
        res = pull_central_update(force=False, timeout=6.0)
        if res.get("updated"):
            print(f"[SLMAgents OTA] Pulled latest update from central repo: {res.get('message')}")
    except Exception as exc:
        print(f"[SLMAgents OTA] Offline / cached startup ({exc})")

    if os.path.isdir(os.path.join(SYNC_ROOT, "slm_harness")) and os.path.isfile(os.path.join(SYNC_ROOT, "main.py")):
        if SYNC_ROOT not in sys.path:
            sys.path.insert(0, SYNC_ROOT)
        return SYNC_ROOT
    return None
