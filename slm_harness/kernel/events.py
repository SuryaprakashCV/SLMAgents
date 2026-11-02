"""
Plane 1: Typed Immutable Event Bus & SSE Streaming Protocol (`EventBus`).

Every kernel state transition, token delta, prefill chunk, tool execution, verifier outcome,
citation batch, and human-in-the-loop pause emits a `HarnessEvent` consumed uniformly by
the Terminal TUI, Headless CLI, Web AI Studio SSE stream, and Waterfall Tracer.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    ROUTE_DECIDED = "route_decided"
    PLAN_CREATED = "plan_created"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    PREFILL_PROGRESS = "prefill_progress"
    REASONING_DELTA = "reasoning_delta"
    TOKEN_DELTA = "token_delta"
    TOOL_CALL_PROPOSED = "tool_call_proposed"
    TOOL_EXEC_STARTED = "tool_exec_started"
    TOOL_RESULT = "tool_result"
    VERIFIER_PASSED = "verifier_passed"
    VERIFIER_FAILED = "verifier_failed"
    CLARIFICATION_NEEDED = "clarification_needed"
    CLARIFICATION_RESOLVED = "clarification_resolved"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    STEER_INJECTED = "steer_injected"
    CHECKPOINT_SAVED = "checkpoint_saved"
    ARTIFACT_CREATED = "artifact_created"
    CITATIONS_EMITTED = "citations_emitted"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"
    RUN_ERROR = "run_error"


TERMINAL_EVENTS = {
    EventType.RUN_COMPLETED,
    EventType.RUN_CANCELLED,
    EventType.RUN_ERROR,
}


@dataclass(frozen=True)
class HarnessEvent:
    event_type: EventType
    run_id: str
    session_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        # Include backward-compatible `type` & `content` keys for legacy Web SSE consumers
        legacy_type = self.event_type.value
        legacy_content: Any = self.payload.get("text", "")

        if self.event_type == EventType.TOKEN_DELTA:
            legacy_type = "token"
            legacy_content = self.payload.get("text", "")
        elif self.event_type in (
            EventType.REASONING_DELTA,
            EventType.ROUTE_DECIDED,
            EventType.STEP_STARTED,
            EventType.TOOL_EXEC_STARTED,
            EventType.VERIFIER_FAILED,
        ):
            legacy_type = "thought"
            legacy_content = self.payload.get("message") or self.payload.get("text") or ""
        elif self.event_type == EventType.RUN_COMPLETED:
            legacy_type = "done"
            legacy_content = self.payload.get("final_text", "")
        elif self.event_type == EventType.RUN_ERROR:
            legacy_type = "error"
            legacy_content = self.payload.get("error", "Unknown error")
        elif self.event_type == EventType.RUN_CANCELLED:
            legacy_type = "cancelled"
            legacy_content = self.payload.get("reason", "Cancelled by user")

        out: Dict[str, Any] = {
            "type": legacy_type,
            "event_type": self.event_type.value,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "content": legacy_content,
            "payload": self.payload,
        }
        if "agent" in self.payload:
            out["agent"] = self.payload["agent"]
        return out

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


class EventBus:
    """Thread-safe synchronous & queue-backed event dispatcher."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._global_listeners: List[Callable[[HarnessEvent], None]] = []
        self._run_queues: Dict[str, List["queue.Queue[HarnessEvent]"]] = {}

    def subscribe(self, callback: Callable[[HarnessEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._global_listeners.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._global_listeners:
                    self._global_listeners.remove(callback)

        return _unsubscribe

    def subscribe_run_queue(self, run_id: str) -> "queue.Queue[HarnessEvent]":
        q: "queue.Queue[HarnessEvent]" = queue.Queue()
        with self._lock:
            self._run_queues.setdefault(run_id, []).append(q)
        return q

    def unsubscribe_run_queue(self, run_id: str, q: "queue.Queue[HarnessEvent]") -> None:
        with self._lock:
            qs = self._run_queues.get(run_id)
            if qs and q in qs:
                qs.remove(q)
            if qs is not None and len(qs) == 0:
                self._run_queues.pop(run_id, None)

    def emit(
        self,
        event_type: EventType,
        run_id: str,
        session_id: str = "default",
        /,
        **payload: Any,
    ) -> HarnessEvent:
        ev = HarnessEvent(
            event_type=event_type,
            run_id=run_id,
            session_id=session_id,
            payload=dict(payload),
        )
        with self._lock:
            listeners = list(self._global_listeners)
            queues = list(self._run_queues.get(run_id, ()))

        for fn in listeners:
            try:
                fn(ev)
            except Exception:
                pass
        for q in queues:
            try:
                q.put_nowait(ev)
            except Exception:
                pass
        return ev

    def iter_run_events(
        self,
        run_id: str,
        q: "queue.Queue[HarnessEvent]",
        poll_timeout: float = 0.25,
    ) -> Iterator[HarnessEvent]:
        """Yields all events for `run_id` until a terminal event is emitted."""
        try:
            while True:
                try:
                    ev = q.get(timeout=poll_timeout)
                except queue.Empty:
                    continue
                yield ev
                if ev.event_type in TERMINAL_EVENTS:
                    break
        finally:
            self.unsubscribe_run_queue(run_id, q)


_GLOBAL_BUS: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _GLOBAL_BUS
    if _GLOBAL_BUS is None:
        _GLOBAL_BUS = EventBus()
    return _GLOBAL_BUS
