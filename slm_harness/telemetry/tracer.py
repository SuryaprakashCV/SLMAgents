"""
Plane 10: Hierarchical Waterfall Span Tracer & JSONL Persistence (`TraceStore`).

Records structured spans for every turn:
`turn -> route -> context_compile -> retrieval -> prefill -> decode -> tool_exec -> verifier`
with CPU RSS delta, TTFT, tok/s, model_id, cache hit status, and ASCII/JSON waterfall rendering
for `/trace` in CLI and the expandable Waterfall drawer in Web AI Studio.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from ..config import get_config
from ..runtime.cpu_topology import get_process_rss_mb


@dataclass
class Span:
    span_id: str
    run_id: str
    name: str
    kind: str  # "turn" | "route" | "context" | "retrieval" | "llm" | "tool" | "verifier" | "governance"
    parent_id: Optional[str] = None
    start_ts: float = field(default_factory=time.time)
    end_ts: Optional[float] = None
    duration_ms: float = 0.0
    rss_start_mb: float = 0.0
    rss_end_mb: float = 0.0
    status: str = "ok"
    attributes: Dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str = "ok", **extra_attrs: Any) -> None:
        self.end_ts = time.time()
        self.duration_ms = round(max(0.0, (self.end_ts - self.start_ts) * 1000.0), 2)
        self.rss_end_mb = get_process_rss_mb()
        self.status = status
        if extra_attrs:
            self.attributes.update(extra_attrs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "span_id": self.span_id,
            "run_id": self.run_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "duration_ms": self.duration_ms,
            "rss_start_mb": self.rss_start_mb,
            "rss_end_mb": self.rss_end_mb,
            "status": self.status,
            "attributes": self.attributes,
        }


@dataclass
class TraceRecord:
    run_id: str
    session_id: str
    query_preview: str
    created_at: float = field(default_factory=time.time)
    spans: List[Span] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def total_ms(self) -> float:
        if not self.spans:
            return 0.0
        root = self.spans[0]
        if root.duration_ms > 0:
            return root.duration_ms
        return round(sum(s.duration_ms for s in self.spans if s.parent_id is None), 2)

    def format_ascii_waterfall(self, width: int = 34) -> str:
        if not self.spans:
            return f"Trace {self.run_id}: (no spans recorded)"
        t0 = min(s.start_ts for s in self.spans)
        t_end = max((s.end_ts or s.start_ts) for s in self.spans)
        span_total = max(1e-4, t_end - t0)
        total_ms = round(span_total * 1000.0, 1)

        lines = [
            f"Trace Waterfall — run_id={self.run_id} | total={total_ms}ms | query={self.query_preview[:56]!r}",
            "-" * 96,
        ]
        depth_map: Dict[str, int] = {}
        for sp in self.spans:
            d = (depth_map.get(sp.parent_id, -1) + 1) if sp.parent_id else 0
            depth_map[sp.span_id] = d
            rel_start = max(0.0, (sp.start_ts - t0) / span_total)
            rel_end = min(1.0, ((sp.end_ts or sp.start_ts) - t0) / span_total)
            c0 = min(width - 1, int(rel_start * width))
            c1 = max(c0 + 1, min(width, int(round(rel_end * width))))
            bar = ("." * c0) + ("#" * (c1 - c0)) + ("." * (width - c1))

            indent = "  " * min(d, 4)
            label = f"{indent}{sp.name}"[:28].ljust(28)
            meta_bits: List[str] = []
            if "model_id" in sp.attributes:
                meta_bits.append(str(sp.attributes["model_id"]))
            if "tok_per_sec" in sp.attributes and sp.attributes["tok_per_sec"]:
                meta_bits.append(f"{sp.attributes['tok_per_sec']} tok/s")
            if "ttft_ms" in sp.attributes and sp.attributes["ttft_ms"]:
                meta_bits.append(f"TTFT={sp.attributes['ttft_ms']}ms")
            if "agent" in sp.attributes:
                meta_bits.append(f"agent={sp.attributes['agent']}")
            if sp.status != "ok":
                meta_bits.append(f"status={sp.status}")
            meta_str = (" | " + ", ".join(meta_bits)) if meta_bits else ""
            lines.append(f"{label} [{bar}] {sp.duration_ms:7.1f}ms{meta_str}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "query_preview": self.query_preview,
            "created_at": self.created_at,
            "total_ms": self.total_ms(),
            "summary": self.summary,
            "spans": [s.to_dict() for s in self.spans],
        }


class TraceStore:
    """Thread-safe in-memory ring buffer + append-only JSONL trace recorder."""

    def __init__(self, traces_dir: Optional[str] = None, max_memory_traces: int = 100) -> None:
        cfg = get_config()
        self.traces_dir = traces_dir or os.path.join(cfg.data_dir, "traces")
        os.makedirs(self.traces_dir, exist_ok=True)
        self.max_memory_traces = max_memory_traces
        self._lock = threading.RLock()
        self._traces: Dict[str, TraceRecord] = {}
        self._order: List[str] = []

    def start_trace(self, run_id: str, session_id: str, query_preview: str = "") -> TraceRecord:
        rec = TraceRecord(
            run_id=run_id,
            session_id=session_id,
            query_preview=query_preview[:160],
        )
        with self._lock:
            self._traces[run_id] = rec
            self._order.append(run_id)
            while len(self._order) > self.max_memory_traces:
                old = self._order.pop(0)
                self._traces.pop(old, None)
        return rec

    def start_span(
        self,
        run_id: str,
        name: str,
        kind: str,
        parent_id: Optional[str] = None,
        **attributes: Any,
    ) -> Span:
        sp = Span(
            span_id=uuid.uuid4().hex[:10],
            run_id=run_id,
            name=name,
            kind=kind,
            parent_id=parent_id,
            rss_start_mb=get_process_rss_mb(),
            attributes=dict(attributes),
        )
        with self._lock:
            rec = self._traces.get(run_id)
            if rec is None:
                rec = TraceRecord(run_id=run_id, session_id="default", query_preview="")
                self._traces[run_id] = rec
                self._order.append(run_id)
            rec.spans.append(sp)
        return sp

    @contextmanager
    def span(
        self,
        run_id: str,
        name: str,
        kind: str,
        parent_id: Optional[str] = None,
        **attributes: Any,
    ) -> Iterator[Span]:
        sp = self.start_span(run_id, name=name, kind=kind, parent_id=parent_id, **attributes)
        try:
            yield sp
            if sp.end_ts is None:
                sp.finish(status="ok")
        except Exception as exc:
            sp.finish(status="error", error=str(exc)[:240])
            raise

    def finalize_trace(self, run_id: str, **summary: Any) -> Optional[TraceRecord]:
        with self._lock:
            rec = self._traces.get(run_id)
            if rec is None:
                return None
            rec.summary.update(summary)
            payload = rec.to_dict()

        log_path = os.path.join(self.traces_dir, "harness_traces.jsonl")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return rec

    def get_trace(self, run_id: Optional[str] = None) -> Optional[TraceRecord]:
        with self._lock:
            if run_id and run_id in self._traces:
                return self._traces[run_id]
            if not run_id and self._order:
                return self._traces.get(self._order[-1])
        return None

    def recent_traces(self, limit: int = 15) -> List[Dict[str, Any]]:
        with self._lock:
            ids = list(reversed(self._order[-limit:]))
            return [self._traces[i].to_dict() for i in ids if i in self._traces]


_GLOBAL_TRACER: Optional[TraceStore] = None


def get_trace_store() -> TraceStore:
    global _GLOBAL_TRACER
    if _GLOBAL_TRACER is None:
        _GLOBAL_TRACER = TraceStore()
    return _GLOBAL_TRACER
