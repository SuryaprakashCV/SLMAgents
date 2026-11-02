"""
Plane 4 (Tier 1): In-RAM Zero-Copy `TurnBlackboard`.

Allows specialist tools and DAG subagents (`SQL -> DataFrame -> Python Sandbox -> Verifier`)
to pass live Python objects, DataFrames, active SQLite database paths, and attached artifacts
within a session without serializing large payloads through LLM tokens.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


class TurnBlackboard:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._timestamps: Dict[str, float] = {}

    def put(self, session_id: str, key: str, value: Any) -> None:
        with self._lock:
            bucket = self._sessions.setdefault(session_id, {})
            bucket[key] = value
            self._timestamps[session_id] = time.time()

    def get(self, session_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._sessions.get(session_id, {}).get(key, default)

    def append_item(self, session_id: str, key: str, item: Any, max_items: int = 32) -> None:
        with self._lock:
            bucket = self._sessions.setdefault(session_id, {})
            lst: List[Any] = list(bucket.get(key) or [])
            if item not in lst:
                lst.append(item)
            bucket[key] = lst[-max_items:]
            self._timestamps[session_id] = time.time()

    def snapshot_keys(self, session_id: str) -> Dict[str, str]:
        with self._lock:
            bucket = self._sessions.get(session_id, {})
            return {k: type(v).__name__ for k, v in bucket.items()}

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._timestamps.pop(session_id, None)


_GLOBAL_BLACKBOARD: Optional[TurnBlackboard] = None


def get_blackboard() -> TurnBlackboard:
    global _GLOBAL_BLACKBOARD
    if _GLOBAL_BLACKBOARD is None:
        _GLOBAL_BLACKBOARD = TurnBlackboard()
    return _GLOBAL_BLACKBOARD
