"""
Plane 8: Capability Risk Governance (`L0`-`L4`), Realpath Workspace Jail & POSIX `rlimit` Sandbox.
"""

from __future__ import annotations

import json
import os
import platform
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional, Sequence, Union

from ..config import ApprovalPolicy, HarnessConfig, get_config
from ..runtime.arena import CancellationToken, RunCancelledError


ALL_FOLDER_PERMISSIONS = ("create", "edit", "delete", "run", "execute")


class FolderPermissionManager:
    """
    Consent-gated folder permission registry for Project Builder Studio.
    Allows the user to grant explicit permissions under any chosen folder path
    to create, edit, delete, run, and execute files/commands.
    """

    def __init__(self, store_path: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        default_dir = os.path.expanduser("~/.slm_harness")
        os.makedirs(default_dir, exist_ok=True)
        self.store_path = store_path or os.path.join(default_dir, "project_permissions.json")
        self._state: Dict[str, Any] = {
            "active_folder": None,
            "folders": {},
            "project_slug_map": {},
        }
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not os.path.isfile(self.store_path):
                return
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self._state["active_folder"] = raw.get("active_folder")
                    self._state["folders"] = dict(raw.get("folders") or {})
                    self._state["project_slug_map"] = dict(raw.get("project_slug_map") or {})
            except Exception:
                pass

    def _save(self) -> None:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
                tmp = self.store_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._state, f, indent=2)
                os.replace(tmp, self.store_path)
            except Exception:
                pass

    def normalize_folder_path(self, raw_path: str) -> str:
        cleaned = (raw_path or "").strip()
        if not cleaned:
            cfg = get_config()
            return os.path.realpath(os.path.join(cfg.workspace_dir, "projects"))
        expanded = os.path.expanduser(cleaned)
        if not os.path.isabs(expanded):
            expanded = os.path.join(get_config().workspace_dir, expanded)
        abs_p = os.path.abspath(expanded)
        parent = os.path.dirname(abs_p)
        if os.path.exists(abs_p):
            return os.path.realpath(abs_p)
        if os.path.exists(parent):
            return os.path.join(os.path.realpath(parent), os.path.basename(abs_p))
        return abs_p

    def find_matching_grant(self, target_path: str) -> Optional[Dict[str, Any]]:
        """
        Finds the most specific granted folder entry that contains `target_path`
        (either exact folder match or any child path under that folder).
        """
        if not target_path:
            return None
        norm_target = self.normalize_folder_path(target_path)
        with self._lock:
            best_match: Optional[Dict[str, Any]] = None
            best_len = -1
            for folder_key, entry in self._state.get("folders", {}).items():
                if not isinstance(entry, dict) or not entry.get("granted_by_user"):
                    continue
                folder_real = self.normalize_folder_path(entry.get("folder_path") or folder_key)
                if norm_target == folder_real or norm_target.startswith(folder_real + os.sep):
                    if len(folder_real) > best_len:
                        best_len = len(folder_real)
                        best_match = dict(entry)
                        best_match["folder_path"] = folder_real
            return best_match

    def check_permission(self, folder_path: str, operation: str = "all") -> Dict[str, Any]:
        norm_path = self.normalize_folder_path(folder_path)
        grant = self.find_matching_grant(norm_path)
        if not grant:
            return {
                "permitted": False,
                "requires_consent": True,
                "folder_path": norm_path,
                "granted_root": None,
                "permissions": {k: False for k in ALL_FOLDER_PERMISSIONS},
                "full_access": False,
                "message": (
                    f"Permission required for '{norm_path}'. Please grant SLMAgents permission "
                    "under this folder to create, edit, delete, run, and execute files."
                ),
            }

        perms = dict(grant.get("permissions") or {})
        full = bool(perms.get("full_access") or all(perms.get(k, False) for k in ALL_FOLDER_PERMISSIONS))
        op_low = (operation or "all").lower()
        if op_low in ("all", "full", "full_access"):
            allowed = full or all(bool(perms.get(k, False)) for k in ALL_FOLDER_PERMISSIONS)
        elif op_low in ("write", "create_or_edit"):
            allowed = full or bool(perms.get("create")) or bool(perms.get("edit"))
        elif op_low == "read":
            allowed = True
        else:
            allowed = full or bool(perms.get(op_low, False))

        return {
            "permitted": allowed,
            "requires_consent": not allowed,
            "folder_path": norm_path,
            "granted_root": grant.get("folder_path"),
            "permissions": {
                "create": bool(perms.get("create", full)),
                "edit": bool(perms.get("edit", full)),
                "delete": bool(perms.get("delete", full)),
                "run": bool(perms.get("run", full)),
                "execute": bool(perms.get("execute", full)),
                "full_access": full,
            },
            "full_access": full,
            "granted_at": grant.get("granted_at"),
            "project_name": grant.get("project_name", ""),
            "project_slug": grant.get("project_slug", ""),
            "message": (
                f"Full permissions active under '{grant.get('folder_path')}' (Create, Edit, Delete, Run, Execute)."
                if allowed
                else f"Missing '{operation}' permission under '{norm_path}'."
            ),
        }

    def is_operation_allowed(self, target_path: str, operation: str = "write") -> bool:
        info = self.check_permission(target_path, operation=operation)
        return bool(info.get("permitted"))

    def grant_permission(
        self,
        folder_path: str,
        permissions: Optional[Dict[str, bool]] = None,
        project_name: str = "",
        project_slug: str = "",
        set_active: bool = True,
        create_if_missing: bool = True,
    ) -> Dict[str, Any]:
        norm_path = self.normalize_folder_path(folder_path)
        for forbidden_dir in ("/.ssh", "/.aws", "/.gnupg"):
            if forbidden_dir in (norm_path + "/"):
                raise SecurityViolationError(f"Cannot grant permission to protected credential path: {norm_path}")

        req_perms = permissions or {}
        create_ok = bool(req_perms.get("create", True))
        edit_ok = bool(req_perms.get("edit", True))
        delete_ok = bool(req_perms.get("delete", True))
        run_ok = bool(req_perms.get("run", True))
        exec_ok = bool(req_perms.get("execute", True))
        full_ok = bool(req_perms.get("full_access", create_ok and edit_ok and delete_ok and run_ok and exec_ok))

        if create_if_missing and create_ok:
            os.makedirs(norm_path, exist_ok=True)
            norm_path = os.path.realpath(norm_path)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "folder_path": norm_path,
            "project_name": project_name or os.path.basename(norm_path) or "Local Project",
            "project_slug": project_slug or re.sub(r"[^a-zA-Z0-9_\-]", "_", os.path.basename(norm_path).lower()),
            "permissions": {
                "create": create_ok,
                "edit": edit_ok,
                "delete": delete_ok,
                "run": run_ok,
                "execute": exec_ok,
                "full_access": full_ok,
            },
            "granted_by_user": True,
            "granted_at": now_iso,
        }
        with self._lock:
            self._state.setdefault("folders", {})[norm_path] = entry
            if set_active:
                self._state["active_folder"] = norm_path
            if project_slug:
                self._state.setdefault("project_slug_map", {})[project_slug] = norm_path
            self._save()
        return self.check_permission(norm_path, operation="all")

    def revoke_permission(self, folder_path: str) -> Dict[str, Any]:
        norm_path = self.normalize_folder_path(folder_path)
        with self._lock:
            folders = self._state.get("folders", {})
            removed = folders.pop(norm_path, None)
            if not removed:
                for k in list(folders.keys()):
                    if self.normalize_folder_path(k) == norm_path:
                        removed = folders.pop(k, None)
            if self._state.get("active_folder") == norm_path:
                remaining = list(folders.keys())
                self._state["active_folder"] = remaining[-1] if remaining else None
            self._save()
        return {
            "revoked": bool(removed),
            "folder_path": norm_path,
            "active_folder": self._state.get("active_folder"),
        }

    def register_project_location(self, project_slug: str, project_dir: str, project_name: str = "") -> None:
        clean_slug = re.sub(r"[^A-Za-z0-9_.\-]", "", project_slug or "")
        if not clean_slug or not project_dir:
            return
        norm_dir = self.normalize_folder_path(project_dir)
        with self._lock:
            self._state.setdefault("project_slug_map", {})[clean_slug] = norm_dir
            if norm_dir in self._state.get("folders", {}):
                self._state["folders"][norm_dir]["project_slug"] = clean_slug
                if project_name:
                    self._state["folders"][norm_dir]["project_name"] = project_name
            self._save()

    def resolve_project_dir(self, project_slug: str, default_ws_dir: Optional[str] = None) -> str:
        clean_slug = re.sub(r"[^A-Za-z0-9_.\-]", "", project_slug or "")
        with self._lock:
            mapped = (self._state.get("project_slug_map") or {}).get(clean_slug)
        if mapped and os.path.isdir(mapped):
            return os.path.abspath(mapped)
        ws = os.path.abspath(default_ws_dir or get_config().workspace_dir)
        return os.path.abspath(os.path.join(ws, "projects", clean_slug))

    def list_granted_folders(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list((self._state.get("folders") or {}).values())
        items.sort(key=lambda x: str(x.get("granted_at") or ""), reverse=True)
        return items

    def get_active_folder(self) -> Optional[str]:
        with self._lock:
            af = self._state.get("active_folder")
            if af and self.check_permission(af).get("permitted"):
                return af
            return None

    def set_active_folder(self, folder_path: Optional[str]) -> Optional[str]:
        with self._lock:
            if not folder_path:
                self._state["active_folder"] = None
            else:
                self._state["active_folder"] = self.normalize_folder_path(folder_path)
            self._save()
            return self._state["active_folder"]


_FOLDER_PERM_MANAGER: Optional[FolderPermissionManager] = None


def get_folder_permission_manager() -> FolderPermissionManager:
    global _FOLDER_PERM_MANAGER
    if _FOLDER_PERM_MANAGER is None:
        _FOLDER_PERM_MANAGER = FolderPermissionManager()
    return _FOLDER_PERM_MANAGER



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

    def resolve_path(
        self,
        user_path: str,
        for_write: bool = False,
        allow_temp: bool = True,
        operation: str = "",
    ) -> str:
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

        eff_op = operation or ("write" if for_write else "read")
        in_permitted_folder = get_folder_permission_manager().is_operation_allowed(real, operation=eff_op)

        if for_write:
            if not (in_ws or in_data or in_tmp or in_permitted_folder):
                raise SecurityViolationError(
                    f"Write outside workspace jail denied: {real} (workspace={self.workspace_real}). "
                    f"Grant folder permission in Project Builder Studio to allow access."
                )
        else:
            if not (in_ws or in_data or in_tmp or in_permitted_folder):
                raise SecurityViolationError(
                    f"Read outside workspace jail denied: {real} (workspace={self.workspace_real}). "
                    f"Grant folder permission in Project Builder Studio to allow access."
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
