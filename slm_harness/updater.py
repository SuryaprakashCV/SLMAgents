"""
Central Repository Over-The-Air (OTA) Auto-Updater for SLMAgents 2.0 Desktop & CLI.

Allows standalone installed Desktop Apps (macOS `.app`, Windows `.exe`, Linux `.AppImage`)
and local servers to automatically detect, pull, and hot-reload updates whenever code,
UI (`chat.html`/`app.js`), or `.slm/skills/` are uploaded or pushed to Git.
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
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CENTRAL_REPO = os.environ.get("SLM_CENTRAL_REPO", "SuryaprakashCV/SLMAgents")
DEFAULT_CENTRAL_BRANCH = os.environ.get("SLM_CENTRAL_BRANCH", "slm-agents-2.0")

# Candidate (repo, branch) pairs checked so uploads to either `slm-agents-2.0` or `main` are picked up automatically
CANDIDATE_GIT_SOURCES: List[Tuple[str, str]] = [
    (DEFAULT_CENTRAL_REPO, DEFAULT_CENTRAL_BRANCH),
    (DEFAULT_CENTRAL_REPO, "main"),
    ("t00114218-stack/SLMAgents", "slm-agents-2.0"),
    ("t00114218-stack/SLMAgents", "main"),
]

SYNC_ROOT = os.path.expanduser("~/.slm_harness/central_sync")
STATE_FILE = os.path.join(SYNC_ROOT, ".sync_state.json")

_AUTO_UPDATER_STARTED = False
_AUTO_UPDATER_LOCK = threading.Lock()


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
    if repo_dir:
        git_sha = get_local_git_sha(repo_dir)
        if git_sha and not state.get("local_sha"):
            state["local_sha"] = git_sha
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
    """Queries GitHub API for the latest commit SHA and message on `(repo, branch)`."""
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
            date_str = (commit_obj.get("committer") or {}).get("date", "")
            return {
                "repo": repo,
                "branch": branch,
                "sha": sha,
                "short_sha": sha[:7] if sha else "",
                "message": (commit_obj.get("message") or "").splitlines()[0],
                "date": date_str,
                "author": (commit_obj.get("author") or {}).get("name", ""),
            }
    except Exception:
        return None


def fetch_newest_remote_commit(timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    """
    Checks candidate GitHub branches (`slm-agents-2.0` and `main`) and returns the commit
    with the newest ISO-8601 committer timestamp.
    """
    best: Optional[Dict[str, Any]] = None
    seen = set()
    for repo, branch in CANDIDATE_GIT_SOURCES:
        if (repo, branch) in seen:
            continue
        seen.add((repo, branch))
        info = fetch_remote_commit_info(repo=repo, branch=branch, timeout=timeout)
        if info and info.get("sha"):
            if best is None or (info.get("date", "") > best.get("date", "")):
                best = info
    return best


def check_for_updates(repo_dir: Optional[str] = None, timeout: float = 3.5) -> Dict[str, Any]:
    if os.environ.get("SPACE_ID"):
        return {
            "status": "ok",
            "update_available": False,
            "central_repo": DEFAULT_CENTRAL_REPO,
            "branch": DEFAULT_CENTRAL_BRANCH,
            "local_sha": "hf-space",
            "remote_sha": "hf-space",
            "message": "Managed by Hugging Face Space container.",
            "auto_sync_enabled": False,
        }
    state = read_sync_state(repo_dir)
    remote = fetch_newest_remote_commit(timeout=timeout)
    git_sha = get_local_git_sha(repo_dir) if repo_dir else None
    local_sha = state.get("local_sha") or git_sha

    if remote is None:
        return {
            "status": "offline",
            "update_available": False,
            "central_repo": state.get("central_repo") or DEFAULT_CENTRAL_REPO,
            "branch": state.get("branch") or DEFAULT_CENTRAL_BRANCH,
            "local_sha": local_sha[:7] if local_sha else "bundled",
            "remote_sha": None,
            "message": "Central repository unreachable (offline mode active).",
        }

    remote_sha = remote["sha"]
    # If local git HEAD already matches remote_sha, record it as synced
    if git_sha and git_sha == remote_sha and local_sha != remote_sha:
        local_sha = remote_sha
        state["local_sha"] = remote_sha
        state["central_repo"] = remote["repo"]
        state["branch"] = remote["branch"]
        _write_sync_state(state)

    update_available = bool(remote_sha and local_sha != remote_sha)
    return {
        "status": "ok",
        "update_available": update_available,
        "central_repo": remote["repo"],
        "branch": remote["branch"],
        "local_sha": local_sha[:7] if local_sha else "bundled",
        "remote_sha": remote["short_sha"],
        "full_remote_sha": remote_sha,
        "latest_commit_message": remote["message"],
        "latest_commit_date": remote["date"],
        "auto_sync_enabled": True,
    }


def _hot_reload_loaded_modules() -> int:
    reloaded = 0
    for mod_name, mod_obj in list(sys.modules.items()):
        if mod_obj is None:
            continue
        if mod_name.startswith(("slm_harness.skills", "slm_harness.router", "slm_harness.verifiers")):
            try:
                importlib.reload(mod_obj)
                reloaded += 1
            except Exception:
                pass
    try:
        from .skills.skill_registry import get_skill_registry

        get_skill_registry().reload()
    except Exception:
        pass
    return reloaded


def pull_central_update(
    repo_dir: Optional[str] = None,
    force: bool = False,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """
    Downloads the latest tarball from GitHub (`codeload.github.com`), extracts updated code,
    skills (`.slm/skills/`), and UI into `~/.slm_harness/central_sync/`, syncs into the
    active workspace if clean/applicable, and hot-reloads active Python modules.
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
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                continue
            dest_path = os.path.join(SYNC_ROOT, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is not None:
                with open(dest_path, "wb") as out_f:
                    shutil.copyfileobj(extracted, out_f)
                updated_files += 1

    # Sync new skills into workspace `.slm/skills/`
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

    # If repo_dir is a git checkout with no uncommitted local edits, fast-forward it too
    if repo_dir and os.path.isdir(os.path.join(repo_dir, ".git")) and not getattr(sys, "frozen", False):
        try:
            dirty = subprocess.run(["git", " status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
            if dirty.returncode == 0 and not dirty.stdout.strip():
                subprocess.run(
                    ["git", "pull", "--ff-only", "github", branch],
                    cwd=repo_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10.0,
                )
        except Exception:
            pass

    if SYNC_ROOT not in sys.path:
        sys.path.insert(0, SYNC_ROOT)

    reloaded_count = _hot_reload_loaded_modules()

    new_state = {
        "central_repo": repo,
        "branch": branch,
        "local_sha": status.get("full_remote_sha"),
        "last_checked": int(time.time()),
        "last_updated": int(time.time()),
        "updated_files": updated_files,
        "latest_commit_message": status.get("latest_commit_message", ""),
    }
    _write_sync_state(new_state)

    return {
        **status,
        "updated": True,
        "update_available": False,
        "local_sha": status.get("remote_sha"),
        "updated_files": updated_files,
        "reloaded_modules": reloaded_count,
        "sync_dir": SYNC_ROOT,
        "message": f"Successfully synced {updated_files} files from {repo}@{status.get('remote_sha')}.",
    }


def activate_central_sync_overlay() -> Optional[str]:
    """
    Called at Desktop App startup (`desktop_app.py`).
    Checks if a newer commit is on GitHub, pulls it into `~/.slm_harness/central_sync/`,
    and activates `~/.slm_harness/central_sync/` as the primary code & UI root.
    """
    if os.environ.get("SPACE_ID") or os.environ.get("SLM_DISABLE_OTA", "0") == "1":
        return None
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


def start_background_auto_updater(repo_dir: Optional[str] = None, interval_sec: float = 45.0) -> None:
    """
    Starts a background daemon thread that polls GitHub every `interval_sec` seconds.
    Whenever the user uploads or pushes a commit to Git, running Desktop Apps and servers
    automatically pull and hot-reload the update with zero manual clicks.
    """
    if os.environ.get("SPACE_ID") or os.environ.get("SLM_DISABLE_OTA", "0") == "1":
        return
    if repo_dir and not os.path.isdir(os.path.join(repo_dir, ".git")) and not getattr(sys, "frozen", False):
        return
    global _AUTO_UPDATER_STARTED
    with _AUTO_UPDATER_LOCK:
        if _AUTO_UPDATER_STARTED:
            return
        _AUTO_UPDATER_STARTED = True

    def _watcher_loop() -> None:
        time.sleep(5.0)
        while True:
            try:
                status = check_for_updates(repo_dir=repo_dir, timeout=4.0)
                if status.get("status") == "ok" and status.get("update_available"):
                    res = pull_central_update(repo_dir=repo_dir, force=False, timeout=20.0)
                    if res.get("updated"):
                        print(f"[SLMAgents Auto-Sync] 🔄 Live update applied from Git: {res.get('message')}")
            except Exception:
                pass
            time.sleep(interval_sec)

    t = threading.Thread(target=_watcher_loop, daemon=True, name="slm-git-auto-updater")
    t.start()
