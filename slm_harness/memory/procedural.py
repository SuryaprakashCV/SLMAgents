"""
Plane 4 (Tier 4): Persistent Procedural Memory, User Facts & Verified Exemplar Cache.

1. Stores cross-session user preferences & project facts (`remember_fact`, `extract_implicit_facts`).
2. Stores verified SQL/Python execution exemplars (`record_verified_exemplar`) and retrieves
   cosine-matched 1-shot references (`sim >= 0.78`) so repeated query patterns get faster and
   more accurate over time on CPU.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import numpy as np

from ..config import get_config
from ..runtime import get_runtime

_IMPLICIT_FACT_PATTERNS = [
    re.compile(r"\bremember that\s+(.{6,220})", re.IGNORECASE),
    re.compile(r"\balways use\s+(.{4,160})", re.IGNORECASE),
    re.compile(r"\bmy preference is\s+(.{4,180})", re.IGNORECASE),
    re.compile(r"\bmy name is\s+([A-Za-z][A-Za-z0-9 _\-]{1,60})", re.IGNORECASE),
]


class ProceduralMemory:
    def __init__(self, db_path: Optional[str] = None) -> None:
        cfg = get_config()
        self.db_path = db_path or os.path.join(cfg.data_dir, "procedural_memory.db")
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
                CREATE TABLE IF NOT EXISTS user_facts (
                    fact_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    fact_text TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL DEFAULT 'general',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS verified_exemplars (
                    exemplar_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    solution_text TEXT NOT NULL,
                    schema_hint TEXT NOT NULL DEFAULT '',
                    embedding BLOB,
                    embed_dim INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ex_domain ON verified_exemplars(domain, created_at DESC);
                """
            )

    def remember_fact(self, fact_text: str, category: str = "preference", workspace: str = "*") -> bool:
        cleaned = fact_text.strip().rstrip(".")
        if len(cleaned) < 4:
            return False
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_facts(fact_id, workspace, fact_text, category, created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:10], workspace, cleaned, category, time.time()),
            )
        return True

    def extract_and_store_implicit_facts(self, user_message: str, workspace: str = "*") -> List[str]:
        stored: List[str] = []
        for pat in _IMPLICIT_FACT_PATTERNS:
            m = pat.search(user_message)
            if m:
                snippet = m.group(0).strip()[:220]
                if self.remember_fact(snippet, category="implicit", workspace=workspace):
                    stored.append(snippet)
        return stored

    def list_facts(self, workspace: Optional[str] = None, limit: int = 15) -> List[str]:
        ws = workspace or get_config().workspace_dir
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT fact_text FROM user_facts WHERE workspace IN ('*', ?) ORDER BY created_at DESC LIMIT ?",
                (ws, int(limit)),
            ).fetchall()
        return [str(r["fact_text"]) for r in rows]

    def delete_fact(self, substring: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM user_facts WHERE fact_text LIKE ?", (f"%{substring}%",))
            return int(cur.rowcount or 0)

    def record_verified_exemplar(
        self,
        domain: str,
        query_text: str,
        solution_text: str,
        schema_hint: str = "",
    ) -> None:
        if not query_text.strip() or not solution_text.strip():
            return
        emb_blob: Optional[bytes] = None
        dim = 0
        try:
            vec = get_runtime().embed_query(query_text[:512])
            emb_blob = np.asarray(vec, dtype=np.float32).tobytes()
            dim = int(vec.shape[0])
        except Exception:
            pass

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO verified_exemplars(
                    exemplar_id, domain, query_text, solution_text, schema_hint, embedding, embed_dim, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex[:10],
                    domain.lower(),
                    query_text.strip()[:800],
                    solution_text.strip()[:2000],
                    schema_hint[:400],
                    emb_blob,
                    dim,
                    time.time(),
                ),
            )

    def retrieve_best_exemplar(
        self,
        domain: str,
        query_text: str,
        min_similarity: float = 0.78,
    ) -> str:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT query_text, solution_text, embedding, embed_dim FROM verified_exemplars WHERE domain = ? ORDER BY created_at DESC LIMIT 60",
                (domain.lower(),),
            ).fetchall()
        if not rows:
            return ""

        try:
            q_vec = get_runtime().embed_query(query_text[:512])
            q_dim = int(q_vec.shape[0])
        except Exception:
            return ""

        best_score = -1.0
        best_txt = ""
        for r in rows:
            if not r["embedding"] or int(r["embed_dim"]) != q_dim:
                continue
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            sim = float(np.dot(q_vec, vec))
            if sim > best_score:
                best_score = sim
                best_txt = f"Similar Verified Task: {r['query_text']}\nVerified Solution:\n{r['solution_text']}"

        return best_txt if best_score >= min_similarity else ""


_GLOBAL_PROCEDURAL: Optional[ProceduralMemory] = None


def get_procedural_memory() -> ProceduralMemory:
    global _GLOBAL_PROCEDURAL
    if _GLOBAL_PROCEDURAL is None:
        _GLOBAL_PROCEDURAL = ProceduralMemory()
    return _GLOBAL_PROCEDURAL
