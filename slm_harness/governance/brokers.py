"""
Plane 8: Interactive Human-in-the-Loop Brokers (`ApprovalBroker` & `ClarificationBroker`).

Supports two seamless execution models:
1. Interactive Synchronous Callback (Terminal TUI `./slm chat` renders a Rich diff/command panel or
   multiple-choice prompt right in the terminal).
2. Event-Driven Ticket Wait / SSE Resolution (Web AI Studio `./slm serve` emits `APPROVAL_REQUIRED`
   or `CLARIFICATION_NEEDED` over SSE and resolves the ticket via `POST /api/runs/{run_id}/approve`
   or `POST /api/runs/{run_id}/clarify` without tearing down the live turn!).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..kernel.events import EventBus, EventType, get_event_bus
from ..runtime.arena import CancellationToken


@dataclass
class ApprovalRequest:
    ticket_id: str
    run_id: str
    session_id: str
    tool_name: str
    risk_level: int
    summary: str
    preview: str  # Unified diff or shell command preview
    arguments: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "summary": self.summary,
            "preview": self.preview,
            "arguments": self.arguments,
            "created_at": self.created_at,
        }


class ApprovalBroker:
    """Manages HITL tool approvals with session-scoped `'allow_always'` memory."""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self.bus = bus or get_event_bus()
        self._lock = threading.RLock()
        self._session_allowlist: Dict[str, Set[str]] = {}
        self._pending: Dict[str, Tuple[ApprovalRequest, threading.Event, List[bool]]] = {}
        self._interactive_handler: Optional[Callable[[ApprovalRequest], Tuple[bool, bool]]] = None

    def set_interactive_handler(
        self, handler: Optional[Callable[[ApprovalRequest], Tuple[bool, bool]]]
    ) -> None:
        """Sets `(req) -> (approved, remember_for_session)` handler for CLI TUI."""
        self._interactive_handler = handler

    def is_preapproved(self, session_id: str, tool_name: str) -> bool:
        with self._lock:
            return tool_name in self._session_allowlist.get(session_id, set())

    def request_approval(
        self,
        run_id: str,
        session_id: str,
        tool_name: str,
        risk_level: int,
        summary: str,
        preview: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 180.0,
        cancel_token: Optional[CancellationToken] = None,
    ) -> bool:
        if self.is_preapproved(session_id, tool_name):
            return True

        req = ApprovalRequest(
            ticket_id=f"apr_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            risk_level=int(risk_level),
            summary=summary,
            preview=preview[:4000],
            arguments=arguments or {},
        )

        # CLI TUI synchronous prompt if registered on foreground thread
        if self._interactive_handler is not None:
            self.bus.emit(EventType.APPROVAL_REQUIRED, run_id, session_id, **req.to_dict())
            approved, remember = self._interactive_handler(req)
            if approved and remember:
                with self._lock:
                    self._session_allowlist.setdefault(session_id, set()).add(tool_name)
            self.bus.emit(
                EventType.APPROVAL_RESOLVED,
                run_id,
                session_id,
                ticket_id=req.ticket_id,
                tool_name=tool_name,
                approved=approved,
            )
            return approved

        # Async ticket wait for Web AI Studio SSE card
        ev = threading.Event()
        decision_box: List[bool] = [False]
        with self._lock:
            self._pending[req.ticket_id] = (req, ev, decision_box)
            # Also index by run_id so `/api/runs/{run_id}/approve` works without ticket_id
            self._pending[f"run:{run_id}"] = (req, ev, decision_box)

        self.bus.emit(EventType.APPROVAL_REQUIRED, run_id, session_id, **req.to_dict())

        deadline = time.time() + timeout_sec
        try:
            while not ev.is_set():
                if cancel_token is not None:
                    cancel_token.check()
                if time.time() >= deadline:
                    return False
                ev.wait(timeout=0.1)
            return bool(decision_box[0])
        finally:
            with self._lock:
                self._pending.pop(req.ticket_id, None)
                self._pending.pop(f"run:{run_id}", None)

    def resolve(
        self,
        ticket_or_run_id: str,
        approved: bool,
        remember_for_session: bool = False,
    ) -> bool:
        with self._lock:
            entry = self._pending.get(ticket_or_run_id) or self._pending.get(f"run:{ticket_or_run_id}")
            if entry is None:
                return False
            req, ev, decision_box = entry
            decision_box[0] = bool(approved)
            if approved and remember_for_session:
                self._session_allowlist.setdefault(req.session_id, set()).add(req.tool_name)
            ev.set()

        self.bus.emit(
            EventType.APPROVAL_RESOLVED,
            req.run_id,
            req.session_id,
            ticket_id=req.ticket_id,
            tool_name=req.tool_name,
            approved=bool(approved),
        )
        return True

    def list_pending(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            out: List[Dict[str, Any]] = []
            seen: Set[str] = set()
            for req, _ev, _box in self._pending.values():
                if req.ticket_id in seen:
                    continue
                if session_id and req.session_id != session_id:
                    continue
                seen.add(req.ticket_id)
                out.append(req.to_dict())
            return out


@dataclass
class ClarificationRequest:
    ticket_id: str
    run_id: str
    session_id: str
    question: str
    options: List[str] = field(default_factory=list)
    context_note: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "question": self.question,
            "options": self.options,
            "context_note": self.context_note,
            "created_at": self.created_at,
        }


class ClarificationBroker:
    """Pauses execution to ask the user 1-3 structured clarifying choices when intent is ambiguous."""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self.bus = bus or get_event_bus()
        self._lock = threading.RLock()
        self._pending: Dict[str, Tuple[ClarificationRequest, threading.Event, List[str]]] = {}
        self._interactive_handler: Optional[Callable[[ClarificationRequest], str]] = None

    def set_interactive_handler(self, handler: Optional[Callable[[ClarificationRequest], str]]) -> None:
        self._interactive_handler = handler

    def ask_user(
        self,
        run_id: str,
        session_id: str,
        question: str,
        options: Optional[List[str]] = None,
        context_note: str = "",
        timeout_sec: float = 180.0,
        cancel_token: Optional[CancellationToken] = None,
    ) -> str:
        req = ClarificationRequest(
            ticket_id=f"clr_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            session_id=session_id,
            question=question,
            options=list(options or []),
            context_note=context_note,
        )

        if self._interactive_handler is not None:
            self.bus.emit(EventType.CLARIFICATION_NEEDED, run_id, session_id, **req.to_dict())
            answer = self._interactive_handler(req)
            self.bus.emit(
                EventType.CLARIFICATION_RESOLVED,
                run_id,
                session_id,
                ticket_id=req.ticket_id,
                answer=answer,
            )
            return answer

        ev = threading.Event()
        ans_box: List[str] = [""]
        with self._lock:
            self._pending[req.ticket_id] = (req, ev, ans_box)
            self._pending[f"run:{run_id}"] = (req, ev, ans_box)

        self.bus.emit(EventType.CLARIFICATION_NEEDED, run_id, session_id, **req.to_dict())

        deadline = time.time() + timeout_sec
        try:
            while not ev.is_set():
                if cancel_token is not None:
                    cancel_token.check()
                if time.time() >= deadline:
                    return req.options[0] if req.options else ""
                ev.wait(timeout=0.1)
            return ans_box[0]
        finally:
            with self._lock:
                self._pending.pop(req.ticket_id, None)
                self._pending.pop(f"run:{run_id}", None)

    def resolve(self, ticket_or_run_id: str, answer: str) -> bool:
        with self._lock:
            entry = self._pending.get(ticket_or_run_id) or self._pending.get(f"run:{ticket_or_run_id}")
            if entry is None:
                return False
            req, ev, ans_box = entry
            ans_box[0] = answer.strip()
            ev.set()

        self.bus.emit(
            EventType.CLARIFICATION_RESOLVED,
            req.run_id,
            req.session_id,
            ticket_id=req.ticket_id,
            answer=answer.strip(),
        )
        return True

    def list_pending(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        with self._lock:
            for key, (req, _ev, _ans) in self._pending.items():
                if key.startswith("run:") or req.ticket_id in seen:
                    continue
                if session_id and req.session_id != session_id:
                    continue
                seen.add(req.ticket_id)
                out.append(req.to_dict())
        return out


_GLOBAL_APPROVALS: Optional[ApprovalBroker] = None
_GLOBAL_CLARIFICATIONS: Optional[ClarificationBroker] = None


def get_approval_broker() -> ApprovalBroker:
    global _GLOBAL_APPROVALS
    if _GLOBAL_APPROVALS is None:
        _GLOBAL_APPROVALS = ApprovalBroker()
    return _GLOBAL_APPROVALS


def get_clarification_broker() -> ClarificationBroker:
    global _GLOBAL_CLARIFICATIONS
    if _GLOBAL_CLARIFICATIONS is None:
        _GLOBAL_CLARIFICATIONS = ClarificationBroker()
    return _GLOBAL_CLARIFICATIONS
