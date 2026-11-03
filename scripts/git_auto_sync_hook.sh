#!/usr/bin/env bash
# ============================================================================
# SLMAgents 2.0 — Automatic Git Commit / Push OTA Broadcaster Hook
# Triggered automatically on `git commit` so every local commit or upload
# is immediately pushed to the central OTA branch (`github/slm-agents-2.0` & `github/main`)
# and synced to the local `~/.slm_harness/central_sync` Desktop App overlay.
# ============================================================================
set -e

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'latest')"
echo "[SLMAgents OTA Hook] Auto-publishing commit ${SHA} to Central OTA Repository (github/slm-agents-2.0)..."

# Run git push + local central_sync update in the background so `git commit` returns immediately
(
  GIT_TERMINAL_PROMPT=0 git push github HEAD:refs/heads/slm-agents-2.0 >/dev/null 2>&1 || true
  GIT_TERMINAL_PROMPT=0 git push github HEAD:refs/heads/main >/dev/null 2>&1 || true
  python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from slm_harness.updater import pull_central_update; pull_central_update(repo_dir='$REPO_ROOT', force=True)" >/dev/null 2>&1 || true
) </dev/null >/dev/null 2>&1 &
exit 0
