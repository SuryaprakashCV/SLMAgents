"""
Plane 1: Durable SQLite WAL Checkpoints, Conversation Branching Tree, Mid-Flight `SteerQueue`,
and Shadow File Snapshot VFS (`/undo` & `/rewind`).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_config


@dataclass
class TurnNode:
    turn_id: str
    session_id: str
    branch_name: str
    parent_turn_id: Optional[str]
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    agent_used: str = ""
    mode: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    run_id: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "branch_name": self.branch_name,
            "parent_turn_id": self.parent_turn_id,
            "role": self.role,
            "content": self.content,
            "agent_used": self.agent_used,
            "mode": self.mode,
            "citations": self.citations,
            "artifacts": self.artifacts,
            "run_id": self.run_id,
            "created_at": self.created_at,
        }


class SteerQueue:
    """Thread-safe mid-flight user steering queue (`POST /api/runs/{id}/steer` or CLI input)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: Dict[str, List[str]] = {}

    def push(self, session_or_run_id: str, hint: str) -> None:
        cleaned = hint.strip()
        if not cleaned:
            return
        with self._lock:
            self._queues.setdefault(session_or_run_id, []).append(cleaned)

    def drain(self, *keys: str) -> List[str]:
        collected: List[str] = []
        with self._lock:
            for k in keys:
                if k and k in self._queues:
                    collected.extend(self._queues.pop(k))
        return collected


class ShadowSnapshotVFS:
    """
    Captures pre-mutation file snapshots per turn so `/undo` and `/rewind <n>` atomically
    restore edited or newly created workspace files.
    """

    def __init__(self, snapshots_root: Optional[str] = None) -> None:
        cfg = get_config()
        self.root = snapshots_root or os.path.join(cfg.data_dir, "snapshots")
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.Lock()

    def _ledger_path(self, session_id: str) -> str:
        sdir = os.path.join(self.root, session_id)
        os.makedirs(sdir, exist_ok=True)
        return os.path.join(sdir, "ledger.json")

    def _load_ledger(self, session_id: str) -> List[Dict[str, Any]]:
        lp = self._ledger_path(session_id)
        if not os.path.isfile(lp):
            return []
        try:
            with open(lp, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_ledger(self, session_id: str, entries: List[Dict[str, Any]]) -> None:
        lp = self._ledger_path(session_id)
        with open(lp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)

    def record_before_write(self, session_id: str, run_id: str, target_path: str) -> None:
        abspath = os.path.abspath(target_path)
        with self._lock:
            ledger = self._load_ledger(session_id)
            # Only snapshot the FIRST state of `abspath` within the same `run_id`
            for item in ledger:
                if item.get("run_id") == run_id and item.get("target_path") == abspath:
                    return

            existed = os.path.isfile(abspath)
            backup_file: Optional[str] = None
            if existed:
                sdir = os.path.join(self.root, session_id, run_id)
                os.makedirs(sdir, exist_ok=True)
                backup_file = os.path.join(sdir, f"{uuid.uuid4().hex[:10]}_{os.path.basename(abspath)}")
                shutil.copy2(abspath, backup_file)

            ledger.append(
                {
                    "run_id": run_id,
                    "target_path": abspath,
                    "existed": existed,
                    "backup_file": backup_file,
                    "timestamp": time.time(),
                }
            )
            self._save_ledger(session_id, ledger)

    def undo_run(self, session_id: str, run_id: Optional[str] = None) -> List[str]:
        """Restores files modified in `run_id` (or the latest run in `session_id`)."""
        restored: List[str] = []
        with self._lock:
            ledger = self._load_ledger(session_id)
            if not ledger:
                return []
            target_run = run_id or ledger[-1]["run_id"]
            to_revert = [e for e in ledger if e["run_id"] == target_run]
            remaining = [e for e in ledger if e["run_id"] != target_run]

            for entry in reversed(to_revert):
                tpath = entry["target_path"]
                if entry["existed"] and entry.get("backup_file") and os.path.isfile(entry["backup_file"]):
                    os.makedirs(os.path.dirname(tpath), exist_ok=True)
                    shutil.copy2(entry["backup_file"], tpath)
                    restored.append(f"restored:{tpath}")
                elif not entry["existed"] and os.path.isfile(tpath):
                    try:
                        os.remove(tpath)
                        restored.append(f"removed_new:{tpath}")
                    except OSError:
                        pass

            self._save_ledger(session_id, remaining)
        return restored


class CheckpointStore:
    """
    SQLite WAL Persistence Engine for Conversation Trees, Branching (`/fork`),
    Rewind (`/rewind`), Episodic Summaries (`/compact`), and Pause/Resume Run Checkpoints.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        cfg = get_config()
        self.db_path = db_path or os.path.join(cfg.data_dir, "harness_state.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    active_branch TEXT NOT NULL DEFAULT 'main',
                    head_turn_id TEXT,
                    compacted_summary TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS branches (
                    session_id TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    head_turn_id TEXT,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (session_id, branch_name)
                );

                CREATE TABLE IF NOT EXISTS turns (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    branch_name TEXT NOT NULL,
                    parent_turn_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    agent_used TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    run_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, created_at);

                CREATE TABLE IF NOT EXISTS run_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    state_name TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ckpt_run ON run_checkpoints(run_id, step_index);
                """
            )

    def ensure_session(self, session_id: str) -> Dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO sessions(session_id, active_branch, head_turn_id, compacted_summary, created_at, updated_at) VALUES (?, 'main', NULL, '', ?, ?)",
                    (session_id, now, now),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO branches(session_id, branch_name, head_turn_id, created_at) VALUES (?, 'main', NULL, ?)",
                    (session_id, now),
                )
                return {
                    "session_id": session_id,
                    "active_branch": "main",
                    "head_turn_id": None,
                    "compacted_summary": "",
                }
            return dict(row)

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        agent_used: str = "",
        mode: str = "",
        citations: Optional[List[Dict[str, Any]]] = None,
        artifacts: Optional[List[str]] = None,
        run_id: str = "",
    ) -> TurnNode:
        sess = self.ensure_session(session_id)
        branch = sess["active_branch"]
        parent_id = sess["head_turn_id"]
        turn_id = uuid.uuid4().hex[:12]
        now = time.time()

        node = TurnNode(
            turn_id=turn_id,
            session_id=session_id,
            branch_name=branch,
            parent_turn_id=parent_id,
            role=role,
            content=content,
            agent_used=agent_used,
            mode=mode,
            citations=citations or [],
            artifacts=artifacts or [],
            run_id=run_id,
            created_at=now,
        )

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, branch_name, parent_turn_id, role, content,
                    agent_used, mode, citations_json, artifacts_json, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.turn_id,
                    node.session_id,
                    node.branch_name,
                    node.parent_turn_id,
                    node.role,
                    node.content,
                    node.agent_used,
                    node.mode,
                    json.dumps(node.citations, ensure_ascii=False),
                    json.dumps(node.artifacts, ensure_ascii=False),
                    node.run_id,
                    node.created_at,
                ),
            )
            conn.execute(
                "UPDATE sessions SET head_turn_id = ?, updated_at = ? WHERE session_id = ?",
                (turn_id, now, session_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO branches(session_id, branch_name, head_turn_id, created_at) VALUES (?, ?, ?, ?)",
                (session_id, branch, turn_id, now),
            )
        return node

    def get_branch_lineage(self, session_id: str, limit: int = 40) -> List[TurnNode]:
        """Walks `parent_turn_id` pointers from active branch head to build exact lineage."""
        sess = self.ensure_session(session_id)
        curr_id = sess.get("head_turn_id")
        if not curr_id:
            return []

        lineage: List[TurnNode] = []
        visited = set()
        with self._lock, self._connect() as conn:
            while curr_id and curr_id not in visited and len(lineage) < limit:
                visited.add(curr_id)
                row = conn.execute("SELECT * FROM turns WHERE turn_id = ?", (curr_id,)).fetchone()
                if row is None:
                    break
                lineage.append(
                    TurnNode(
                        turn_id=row["turn_id"],
                        session_id=row["session_id"],
                        branch_name=row["branch_name"],
                        parent_turn_id=row["parent_turn_id"],
                        role=row["role"],
                        content=row["content"],
                        agent_used=row["agent_used"],
                        mode=row["mode"],
                        citations=json.loads(row["citations_json"] or "[]"),
                        artifacts=json.loads(row["artifacts_json"] or "[]"),
                        run_id=row["run_id"],
                        created_at=row["created_at"],
                    )
                )
                curr_id = row["parent_turn_id"]

        lineage.reverse()
        return lineage

    def fork_branch(self, session_id: str, new_branch_name: str) -> Dict[str, Any]:
        """Creates or switches to `new_branch_name` anchored at current `head_turn_id`."""
        sess = self.ensure_session(session_id)
        head = sess.get("head_turn_id")
        now = time.time()
        clean_branch = new_branch_name.strip() or f"fork-{uuid.uuid4().hex[:6]}"

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT head_turn_id FROM branches WHERE session_id = ? AND branch_name = ?",
                (session_id, clean_branch),
            ).fetchone()
            target_head = existing["head_turn_id"] if existing is not None else head
            if existing is None:
                conn.execute(
                    "INSERT INTO branches(session_id, branch_name, head_turn_id, created_at) VALUES (?, ?, ?, ?)",
                    (session_id, clean_branch, target_head, now),
                )
            conn.execute(
                "UPDATE sessions SET active_branch = ?, head_turn_id = ?, updated_at = ? WHERE session_id = ?",
                (clean_branch, target_head, now, session_id),
            )
        return {"session_id": session_id, "active_branch": clean_branch, "head_turn_id": target_head}

    def list_branches(self, session_id: str) -> List[Dict[str, Any]]:
        sess = self.ensure_session(session_id)
        active = sess["active_branch"]
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT branch_name, head_turn_id, created_at FROM branches WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [
            {
                "branch_name": r["branch_name"],
                "head_turn_id": r["head_turn_id"],
                "active": r["branch_name"] == active,
            }
            for r in rows
        ]

    def rewind_turns(
        self,
        session_id: str,
        turns_to_pop: int = 1,
        vfs: Optional[ShadowSnapshotVFS] = None,
    ) -> Dict[str, Any]:
        """Rewinds conversation head back `turns_to_pop` user-assistant pairs and reverts file edits."""
        lineage = self.get_branch_lineage(session_id, limit=200)
        if not lineage:
            return {"rewound_nodes": 0, "restored_files": [], "new_head": None}

        # Pop `turns_to_pop` user exchanges (each typically 2 nodes: user + assistant)
        nodes_to_drop = min(len(lineage), max(1, turns_to_pop * 2))
        dropped = lineage[-nodes_to_drop:]
        remaining = lineage[:-nodes_to_drop]
        new_head = remaining[-1].turn_id if remaining else None

        restored_files: List[str] = []
        if vfs is not None:
            seen_runs = set()
            for node in reversed(dropped):
                if node.run_id and node.run_id not in seen_runs:
                    seen_runs.add(node.run_id)
                    restored_files.extend(vfs.undo_run(session_id, node.run_id))

        sess = self.ensure_session(session_id)
        branch = sess["active_branch"]
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET head_turn_id = ?, updated_at = ? WHERE session_id = ?",
                (new_head, now, session_id),
            )
            conn.execute(
                "UPDATE branches SET head_turn_id = ? WHERE session_id = ? AND branch_name = ?",
                (new_head, session_id, branch),
            )
        return {
            "rewound_nodes": len(dropped),
            "restored_files": restored_files,
            "new_head": new_head,
        }

    def set_compacted_summary(self, session_id: str, summary_text: str, keep_recent_nodes: int = 4) -> None:
        self.ensure_session(session_id)
        lineage = self.get_branch_lineage(session_id, limit=100)
        if len(lineage) > keep_recent_nodes and keep_recent_nodes > 0:
            # Break parent link above `keep_recent_nodes` so older compacted turns aren't re-expanded
            anchor = lineage[-keep_recent_nodes]
            with self._lock, self._connect() as conn:
                conn.execute("UPDATE turns SET parent_turn_id = NULL WHERE turn_id = ?", (anchor.turn_id,))
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET compacted_summary = ?, updated_at = ? WHERE session_id = ?",
                (summary_text.strip(), time.time(), session_id),
            )

    def get_compacted_summary(self, session_id: str) -> str:
        sess = self.ensure_session(session_id)
        return str(sess.get("compacted_summary") or "")

    def clear_session(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM branches WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM run_checkpoints WHERE session_id = ?", (session_id,))

    def save_run_checkpoint(
        self,
        run_id: str,
        session_id: str,
        step_index: int,
        state_name: str,
        state_payload: Dict[str, Any],
    ) -> str:
        ckpt_id = f"{run_id}_s{step_index}_{uuid.uuid4().hex[:6]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO run_checkpoints(checkpoint_id, run_id, session_id, step_index, state_name, state_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ckpt_id,
                    run_id,
                    session_id,
                    int(step_index),
                    state_name,
                    json.dumps(state_payload, ensure_ascii=False, default=str),
                    time.time(),
                ),
            )
        return ckpt_id

    def load_latest_run_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_checkpoints WHERE run_id = ? ORDER BY step_index DESC, created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "checkpoint_id": row["checkpoint_id"],
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "step_index": row["step_index"],
            "state_name": row["state_name"],
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
        }


_GLOBAL_CHECKPOINTS: Optional[CheckpointStore] = None
_GLOBAL_STEER: Optional[SteerQueue] = None
_GLOBAL_VFS: Optional[ShadowSnapshotVFS] = None


def get_checkpoint_store() -> CheckpointStore:
    global _GLOBAL_CHECKPOINTS
    if _GLOBAL_CHECKPOINTS is None:
        _GLOBAL_CHECKPOINTS = CheckpointStore()
    return _GLOBAL_CHECKPOINTS


def get_steer_queue() -> SteerQueue:
    global _GLOBAL_STEER
    if _GLOBAL_STEER is None:
        _GLOBAL_STEER = SteerQueue()
    return _GLOBAL_STEER


def get_shadow_vfs() -> ShadowSnapshotVFS:
    global _GLOBAL_VFS
    if _GLOBAL_VFS is None:
        _GLOBAL_VFS = ShadowSnapshotVFS()
    return _GLOBAL_VFS
