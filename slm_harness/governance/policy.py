"""
Plane 8: Capability Risk Governance (`L0`-`L4`), Realpath Workspace Jail & POSIX `rlimit` Sandbox.
"""

from __future__ import annotations

import os
import platform
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, List, Optional, Sequence, Union

from ..config import ApprovalPolicy, HarnessConfig, get_config
from ..runtime.arena import CancellationToken, RunCancelledError


class RiskLevel(IntEnum):
    L0_PURE = 0              # Math, pure string/JSON transform, memory recall
    L1_WORKSPACE_READ = 1    # fs.read, fs.glob, fs.grep, sqlite SELECT, rag search
    L2_SANDBOX_COMPUTE = 2   # Sandboxed Python DataFrame/math execution in temp scratchpad
    L3_WORKSPACE_MUTATE = 3  # fs.write, fs.edit, git commit inside workspace
    L4_SYSTEM_OR_NET = 4     # Arbitrary shell, package install, destructive rm, external web mutation


class SecurityViolationError(PermissionError):
    """Raised when a tool attempts to escape the workspace jail or violates active policy."""


_SENSITIVE_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}

_DESTRUCTIVE_SHELL_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\b|rm\s+-[a-zA-Z]*r[a-zA-Z]*f?\b|mkfs\b|dd\s+if=|chmod\s+-R\s+777|chown\s+-R|shutdown\b|reboot\b|:\(\)\{)"
    r"|(?:curl|wget)\b[^|]*\|\s*(?:sh|bash|zsh)\b",
    re.IGNORECASE,
)

_READONLY_SHELL_CMDS = {
    "ls",
    "pwd",
    "whoami",
    "date",
    "uname",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "rg",
    "find",
    "which",
    "du",
    "df",
    "echo",
    "stat",
}


class WorkspaceJail:
    """Enforces `os.path.realpath` containment within workspace & harness scratchpad roots."""

    def __init__(self, config: Optional[HarnessConfig | str] = None) -> None:
        if isinstance(config, str):
            self.workspace_real = os.path.realpath(config)
            self.data_real = os.path.realpath(os.path.join(config, ".slm"))
        else:
            cfg = config or get_config()
            self.workspace_real = os.path.realpath(cfg.workspace_dir)
            self.data_real = os.path.realpath(cfg.data_dir)
        self.tmp_real = os.path.realpath(tempfile.gettempdir())

    def resolve_path(self, user_path: str, for_write: bool = False, allow_temp: bool = True) -> str:
        expanded = os.path.expanduser(user_path.strip())
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.workspace_real, expanded)
        real = os.path.realpath(expanded)

        base = os.path.basename(real)
        if base in _SENSITIVE_BASENAMES and for_write:
            raise SecurityViolationError(f"Write blocked to sensitive secret file: {base}")

        for forbidden_dir in ("/.ssh/", "/.aws/", "/.gnupg/"):
            if forbidden_dir in (real + "/"):
                raise SecurityViolationError(f"Access denied to protected credential path: {user_path}")

        if for_write and (real.endswith("/.git/config") or "/.git/hooks/" in real):
            raise SecurityViolationError("Write denied to .git/config or .git/hooks")

        in_ws = real == self.workspace_real or real.startswith(self.workspace_real + os.sep)
        in_data = real == self.data_real or real.startswith(self.data_real + os.sep)
        in_tmp = allow_temp and (real == self.tmp_real or real.startswith(self.tmp_real + os.sep))

        if for_write:
            if not (in_ws or in_data or in_tmp):
                raise SecurityViolationError(
                    f"Write outside workspace jail denied: {real} (workspace={self.workspace_real})"
                )
        else:
            if not (in_ws or in_data or in_tmp):
                raise SecurityViolationError(
                    f"Read outside workspace jail denied: {real} (workspace={self.workspace_real})"
                )
        return real


def classify_shell_command_risk(command: str) -> RiskLevel:
    cmd = command.strip()
    if not cmd:
        return RiskLevel.L0_PURE
    if _DESTRUCTIVE_SHELL_RE.search(cmd):
        return RiskLevel.L4_SYSTEM_OR_NET
    if any(op in cmd for op in ("&&", "||", ";", "|", ">", ">>", "$(", "`")):
        # Chained or redirected shell commands default to L3 or L4
        if ">" in cmd or "rm " in cmd or "mv " in cmd:
            return RiskLevel.L3_WORKSPACE_MUTATE
        return RiskLevel.L3_WORKSPACE_MUTATE
    parts = cmd.split()
    prog = os.path.basename(parts[0])
    if prog == "git" and len(parts) >= 2 and parts[1] in ("status", "log", "diff", "branch", "show"):
        return RiskLevel.L1_WORKSPACE_READ
    if prog in _READONLY_SHELL_CMDS:
        return RiskLevel.L1_WORKSPACE_READ
    if prog in ("pip", "npm", "brew", "apt", "curl", "wget", "ssh", "scp", "docker"):
        return RiskLevel.L4_SYSTEM_OR_NET
    return RiskLevel.L3_WORKSPACE_MUTATE


def evaluate_capability(
    risk: RiskLevel,
    policy: ApprovalPolicy,
    command_hint: str = "",
) -> str:
    """Returns `'allow'`, `'ask'`, or `'deny'` based on active `ApprovalPolicy`."""
    effective_risk = risk
    if command_hint and _DESTRUCTIVE_SHELL_RE.search(command_hint):
        effective_risk = RiskLevel.L4_SYSTEM_OR_NET

    if policy == ApprovalPolicy.STRICT_READONLY:
        return "allow" if effective_risk <= RiskLevel.L1_WORKSPACE_READ else "deny"

    if policy == ApprovalPolicy.ASK_ALL:
        return "allow" if effective_risk <= RiskLevel.L1_WORKSPACE_READ else "ask"

    if policy == ApprovalPolicy.AUTONOMOUS:
        return "allow" if effective_risk <= RiskLevel.L3_WORKSPACE_MUTATE else "ask"

    # Default: ApprovalPolicy.AUTO_SAFE
    if effective_risk <= RiskLevel.L2_SANDBOX_COMPUTE:
        return "allow"
    return "ask"


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: float


_SECRET_ENV_SUBSTRINGS = ("SECRET", "TOKEN", "API_KEY", "PASSWORD", "PRIVATE_KEY", "CREDENTIAL")


def run_sandboxed_subprocess(
    cmd: Union[str, Sequence[str]],
    cwd: Optional[str] = None,
    timeout_sec: float = 15.0,
    cpu_seconds_limit: int = 15,
    memory_limit_mb: int = 768,
    max_output_bytes: int = 65536,
    shell: bool = False,
    extra_env: Optional[Dict[str, str]] = None,
    cancel_token: Optional[CancellationToken] = None,
) -> SubprocessResult:
    """
    Runs a child process inside a POSIX process-group + `rlimit` sandbox with secret env scrubbing
    and `<50ms` cooperative cancellation support.
    """
    cfg = get_config()
    work_dir = cwd or cfg.workspace_dir

    clean_env: Dict[str, str] = {}
    for k, v in os.environ.items():
        if any(sub in k.upper() for sub in _SECRET_ENV_SUBSTRINGS):
            continue
        clean_env[k] = v
    clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
    clean_env["TOKENIZERS_PARALLELISM"] = "false"
    if extra_env:
        clean_env.update(extra_env)

    is_linux = platform.system() == "Linux"

    def _preexec() -> None:
        os.setsid()
        try:
            import resource

            cpu_lim = max(2, int(cpu_seconds_limit))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_lim, cpu_lim + 1))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
            # Apply RLIMIT_AS only on Linux (macOS arm64 reserves multi-GB virtual space for dyld/accelerate)
            if is_linux and memory_limit_mb > 0:
                mem_bytes = int(memory_limit_mb) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except Exception:
            pass

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=work_dir,
        env=clean_env,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_preexec if os.name == "posix" else None,
    )

    timed_out = False
    deadline = time.time() + max(0.5, float(timeout_sec))

    try:
        while proc.poll() is None:
            if cancel_token is not None and cancel_token.is_cancelled:
                _kill_proc_group(proc)
                raise RunCancelledError(cancel_token.reason or "Subprocess cancelled")
            if time.time() >= deadline:
                timed_out = True
                _kill_proc_group(proc)
                break
            time.sleep(0.03)

        out_b, err_b = proc.communicate(timeout=1.0)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_proc_group(proc)
        out_b, err_b = b"", b"Process timed out"

    dur_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    out_str = (out_b or b"")[:max_output_bytes].decode("utf-8", errors="replace")
    err_str = (err_b or b"")[:max_output_bytes].decode("utf-8", errors="replace")
    if len(out_b or b"") > max_output_bytes:
        out_str += f"\n... [stdout truncated at {max_output_bytes} bytes]"
    if len(err_b or b"") > max_output_bytes:
        err_str += f"\n... [stderr truncated at {max_output_bytes} bytes]"

    return SubprocessResult(
        returncode=proc.returncode if proc.returncode is not None else -9,
        stdout=out_str,
        stderr=err_str,
        timed_out=timed_out,
        duration_ms=dur_ms,
    )


def _kill_proc_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
