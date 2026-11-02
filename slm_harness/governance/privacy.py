"""
Plane 8: Outbound Secret / PII Scrubber & User-Configurable Lifecycle Hook Runner.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Dict, List, Optional

from ..config import HarnessConfig, get_config

_SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{24,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{31,}\b"), "[REDACTED_GOOGLE_API_KEY]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]{24,}=*"), r"\1[REDACTED_BEARER_TOKEN]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
]

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){15,16}\b"), "[REDACTED_CARD]"),
]


def scrub_secrets_and_pii(text: str, scrub_pii: bool = False) -> str:
    if not text:
        return text
    cleaned = text
    for pat, repl in _SECRET_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    if scrub_pii:
        for pat, repl in _PII_PATTERNS:
            cleaned = pat.sub(repl, cleaned)
    return cleaned


def run_lifecycle_hooks(
    hook_event: str,
    payload: Dict[str, Any],
    config: Optional[HarnessConfig] = None,
) -> List[Dict[str, Any]]:
    """
    Executes `.slm/config.yaml` lifecycle hooks (`pre_tool`, `post_tool`, `on_complete`)
    passing sanitized JSON metadata via stdin. Non-zero exit on `pre_tool` vetoes tool execution.
    """
    cfg = config or get_config()
    commands = cfg.hooks.get(hook_event) or []
    if not commands:
        return []

    stdin_bytes = scrub_secrets_and_pii(json.dumps(payload, default=str)).encode("utf-8")
    outcomes: List[Dict[str, Any]] = []
    for cmd in commands:
        try:
            res = subprocess.run(
                cmd,
                input=stdin_bytes,
                shell=True,
                cwd=cfg.workspace_dir,
                capture_output=True,
                timeout=5.0,
                check=False,
            )
            outcomes.append(
                {
                    "command": cmd,
                    "returncode": res.returncode,
                    "stdout": res.stdout.decode("utf-8", errors="ignore")[:500].strip(),
                    "stderr": res.stderr.decode("utf-8", errors="ignore")[:500].strip(),
                }
            )
        except Exception as exc:
            outcomes.append({"command": cmd, "returncode": -1, "error": str(exc)})
    return outcomes
