#!/usr/bin/env bash
# SLMAgents 2.0 — macOS Double-Clickable Desktop App Launcher
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ -x "${SCRIPT_DIR}/.venv/bin/python" ]; then
    PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "Python 3.10+ is required. Install from https://www.python.org/downloads/macos/"
    read -r -p "Press Enter to exit..."
    exit 1
fi

export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/desktop_app.py" "$@"
