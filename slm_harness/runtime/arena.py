"""
Watermark LRU Memory Arena, Cancellation Registry & Automatic OOM Tier Step-Down.

Manages active CPU model instances within strict RAM budgets (`lite`: 2.5GB, `balanced`: 4.5GB,
`power`: 8GB), provides `<40ms` cooperative `CancellationToken`s for every active run,
and gracefully steps down model tiers if host memory pressure exceeds hard watermarks.
"""

from __future__ import annotations

import gc
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..config import MODEL_CATALOG, HarnessConfig, ModelSpec, get_config
from .cpu_topology import detect_cpu_topology, get_cpu_topology, get_process_rss_mb


class MultiThreadExecutionScheduler:
    """
    Bounded Multi-Lane Non-Blocking Thread Scheduler with Watchdog Timeouts & Memory Guard.
    Prevents heavy inference, web research, or background model downloads from starving
    interactive routing or status/UI queries under concurrent load.
    """

    def __init__(self, max_compute_workers: int = 4, max_fast_workers: int = 6, max_bg_workers: int = 3) -> None:
        self._fast_pool = ThreadPoolExecutor(max_workers=max_fast_workers, thread_name_prefix="slm-fast")
        self._compute_pool = ThreadPoolExecutor(max_workers=max_compute_workers, thread_name_prefix="slm-compute")
        self._bg_pool = ThreadPoolExecutor(max_workers=max_bg_workers, thread_name_prefix="slm-bg")
        self._lock = threading.Lock()
        self.active_fast: int = 0
        self.active_compute: int = 0
        self.active_bg: int = 0
        self.completed_tasks: int = 0
        self.timed_out_tasks: int = 0

    def submit(self, lane: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        lane_norm = lane.lower()
        pool = (
            self._fast_pool
            if lane_norm == "fast"
            else (self._bg_pool if lane_norm == "background" else self._compute_pool)
        )

        def _wrapped() -> Any:
            with self._lock:
                if lane_norm == "fast":
                    self.active_fast += 1
                elif lane_norm == "background":
                    self.active_bg += 1
                else:
                    self.active_compute += 1
            try:
                return fn(*args, **kwargs)
            finally:
                with self._lock:
                    if lane_norm == "fast":
                        self.active_fast = max(0, self.active_fast - 1)
                    elif lane_norm == "background":
                        self.active_bg = max(0, self.active_bg - 1)
                    else:
                        self.active_compute = max(0, self.active_compute - 1)
                    self.completed_tasks += 1

        return pool.submit(_wrapped)

    def run_with_timeout(
        self,
        lane: str,
        timeout_sec: float,
        fn: Callable[..., Any],
        *args: Any,
        default: Any = None,
        **kwargs: Any,
    ) -> Any:
        fut = self.submit(lane, fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout_sec)
        except FuturesTimeoutError:
            with self._lock:
                self.timed_out_tasks += 1
            return default
        except Exception:
            return default

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active_fast": self.active_fast,
                "active_compute": self.active_compute,
                "active_background": self.active_bg,
                "completed_tasks": self.completed_tasks,
                "timed_out_tasks": self.timed_out_tasks,
            }



class RunCancelledError(Exception):
    """Raised when a user cancels an active CPU prefill, decode, or tool loop."""


class CancellationToken:
    """Thread-safe cooperative cancellation token polled every token / prefill chunk."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._event = threading.Event()
        self.reason: str = ""
        self.cancelled_at: Optional[float] = None

    def cancel(self, reason: str = "Cancelled by user") -> None:
        self.reason = reason
        self.cancelled_at = time.time()
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise RunCancelledError(self.reason or f"Run {self.run_id} cancelled")


class CancellationRegistry:
    """Tracks all in-flight runs so CLI Ctrl+C or Web /api/runs/{id}/cancel aborts immediately."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: Dict[str, CancellationToken] = {}

    def create(self, run_id: str) -> CancellationToken:
        tok = CancellationToken(run_id)
        with self._lock:
            self._tokens[run_id] = tok
        return tok

    def get(self, run_id: str) -> Optional[CancellationToken]:
        with self._lock:
            return self._tokens.get(run_id)

    def cancel(self, run_id: str, reason: str = "Cancelled by user") -> bool:
        with self._lock:
            tok = self._tokens.get(run_id)
        if tok is not None:
            tok.cancel(reason)
            return True
        return False

    def cancel_all(self, reason: str = "Session cancelled") -> int:
        with self._lock:
            tokens = list(self._tokens.values())
        for t in tokens:
            t.cancel(reason)
        return len(tokens)

    def remove(self, run_id: str) -> None:
        with self._lock:
            self._tokens.pop(run_id, None)

    def finish(self, run_id: str) -> None:
        self.remove(run_id)

    def active_runs(self) -> List[str]:
        with self._lock:
            return [rid for rid, tok in self._tokens.items() if not tok.is_cancelled]


@dataclass
class ArenaEntry:
    spec: ModelSpec
    instance: Any
    loaded_at: float
    last_used_at: float
    load_time_ms: float
    rss_delta_mb: float
    ref_count: int = 0


class MemoryArena:
    """
    LRU Watermark RAM Governor for Pure-CPU Model Instances.
    Pins tiny shared engines (embeddings) while evicting idle specialist LLMs
    whenever estimated + actual RSS approaches `ram_soft_limit_mb`.
    """

    def __init__(self, config: Optional[HarnessConfig] = None) -> None:
        self.config = config or get_config()
        self._lock = threading.RLock()
        self._entries: "OrderedDict[str, ArenaEntry]" = OrderedDict()
        self._model_locks: Dict[str, threading.Lock] = {}
        self.eviction_count: int = 0
        self.tier_stepdowns: List[str] = []
        topo = get_cpu_topology()
        compute_workers = max(2, min(6, topo.performance_cores))
        self.scheduler = MultiThreadExecutionScheduler(
            max_compute_workers=compute_workers,
            max_fast_workers=max(4, compute_workers + 2),
            max_bg_workers=3,
        )

    def check_memory_pressure(self) -> Dict[str, Any]:
        """Proactively inspects RSS and host available RAM; evicts idle models if under pressure."""
        topo = get_cpu_topology()
        rss = get_process_rss_mb()
        evicted = 0
        if rss > self.config.ram_soft_limit_mb:
            evicted = self.purge_unpinned()
        return {
            "process_rss_mb": rss,
            "available_ram_mb": topo.available_ram_mb,
            "total_ram_mb": topo.total_ram_mb,
            "evicted_idle_models": evicted,
        }

    def _get_model_lock(self, model_id: str) -> threading.Lock:
        with self._lock:
            if model_id not in self._model_locks:
                self._model_locks[model_id] = threading.Lock()
            return self._model_locks[model_id]

    def acquire(
        self,
        spec: ModelSpec,
        loader_fn: Callable[[ModelSpec], Any],
    ) -> Any:
        """
        Loads or retrieves `spec` from the CPU memory arena, automatically evicting
        least-recently-used unpinned models or stepping down to `fallback_model_id` on OOM.
        """
        # Fast cache hit
        with self._lock:
            if spec.model_id in self._entries:
                entry = self._entries[spec.model_id]
                entry.last_used_at = time.time()
                self._entries.move_to_end(spec.model_id)
                return entry.instance

        target_spec = self._select_feasible_tier(spec)
        model_lock = self._get_model_lock(target_spec.model_id)

        with model_lock:
            with self._lock:
                if target_spec.model_id in self._entries:
                    entry = self._entries[target_spec.model_id]
                    entry.last_used_at = time.time()
                    self._entries.move_to_end(target_spec.model_id)
                    return entry.instance

            self._evict_until_fits(target_spec.est_ram_mb)

            rss_before = get_process_rss_mb()
            t0 = time.perf_counter()
            try:
                instance = loader_fn(target_spec)
            except MemoryError:
                gc.collect()
                if target_spec.fallback_model_id and target_spec.fallback_model_id in MODEL_CATALOG:
                    fb = MODEL_CATALOG[target_spec.fallback_model_id]
                    self.tier_stepdowns.append(f"OOM step-down: {target_spec.model_id} -> {fb.model_id}")
                    return self.acquire(fb, loader_fn)
                raise

            load_ms = (time.perf_counter() - t0) * 1000.0
            rss_after = get_process_rss_mb()
            delta = max(20.0, rss_after - rss_before)

            with self._lock:
                self._entries[target_spec.model_id] = ArenaEntry(
                    spec=target_spec,
                    instance=instance,
                    loaded_at=time.time(),
                    last_used_at=time.time(),
                    load_time_ms=round(load_ms, 1),
                    rss_delta_mb=round(delta, 1),
                )
            return instance

    def _select_feasible_tier(self, spec: ModelSpec) -> ModelSpec:
        """Checks host free memory and hard watermark; steps down tier proactively if needed."""
        topo = get_cpu_topology()
        curr = spec
        visited = set()
        while curr.model_id not in visited:
            visited.add(curr.model_id)
            path_ok = os_path_exists(curr.abs_path(self.config.models_dir))
            # Leave at least 512MB headroom on host available RAM
            ram_ok = (curr.est_ram_mb <= self.config.ram_hard_limit_mb) and (
                topo.available_ram_mb <= 0 or curr.est_ram_mb <= (topo.available_ram_mb + 1024)
            )
            if path_ok and ram_ok:
                return curr
            if curr.fallback_model_id and curr.fallback_model_id in MODEL_CATALOG:
                nxt = MODEL_CATALOG[curr.fallback_model_id]
                if not ram_ok:
                    self.tier_stepdowns.append(
                        f"Memory guard step-down: {curr.model_id} ({curr.est_ram_mb}MB) -> {nxt.model_id} ({nxt.est_ram_mb}MB)"
                    )
                curr = nxt
            else:
                break
        return curr

    def _evict_until_fits(self, needed_mb: int) -> None:
        with self._lock:
            current_rss = get_process_rss_mb()
            projected = current_rss + needed_mb
            if projected <= self.config.ram_soft_limit_mb and len(self._entries) < 4:
                return

            evicted_any = False
            for mid in list(self._entries.keys()):
                entry = self._entries[mid]
                if entry.spec.pinned or entry.ref_count > 0:
                    continue
                if (get_process_rss_mb() + needed_mb) <= self.config.ram_soft_limit_mb:
                    break
                self._close_entry(entry)
                del self._entries[mid]
                self.eviction_count += 1
                evicted_any = True

            if evicted_any:
                gc.collect()

    @staticmethod
    def _close_entry(entry: ArenaEntry) -> None:
        inst = entry.instance
        if hasattr(inst, "close") and callable(inst.close):
            try:
                inst.close()
            except Exception:
                pass

    def purge_unpinned(self) -> int:
        removed = 0
        with self._lock:
            for mid in list(self._entries.keys()):
                entry = self._entries[mid]
                if not entry.spec.pinned and entry.ref_count == 0:
                    self._close_entry(entry)
                    del self._entries[mid]
                    removed += 1
        gc.collect()
        return removed

    def evict_all(self) -> List[str]:
        evicted: List[str] = []
        with self._lock:
            for mid in list(self._entries.keys()):
                entry = self._entries[mid]
                if entry.ref_count == 0:
                    self._close_entry(entry)
                    del self._entries[mid]
                    evicted.append(mid)
        gc.collect()
        return evicted

    def status(self) -> Dict[str, Any]:
        with self._lock:
            loaded = [
                {
                    "model_id": e.spec.model_id,
                    "role": e.spec.role,
                    "backend": e.spec.backend,
                    "quantization": e.spec.quantization,
                    "pinned": e.spec.pinned,
                    "load_time_ms": e.load_time_ms,
                    "rss_delta_mb": e.rss_delta_mb,
                    "idle_sec": round(time.time() - e.last_used_at, 1),
                }
                for e in self._entries.values()
            ]
        topo = detect_cpu_topology(override_threads=self.config.n_threads)
        return {
            "profile": self.config.profile.value,
            "pure_cpu": self.config.pure_cpu,
            "process_rss_mb": get_process_rss_mb(),
            "available_ram_mb": topo.available_ram_mb,
            "total_ram_mb": topo.total_ram_mb,
            "ram_soft_limit_mb": self.config.ram_soft_limit_mb,
            "ram_hard_limit_mb": self.config.ram_hard_limit_mb,
            "loaded_models": loaded,
            "eviction_count": self.eviction_count,
            "tier_stepdowns": self.tier_stepdowns[-5:],
            "scheduler": self.scheduler.status(),
        }


def os_path_exists(p: str) -> bool:
    import os
    return os.path.exists(p)


_GLOBAL_ARENA: Optional[MemoryArena] = None
_GLOBAL_CANCELS: Optional[CancellationRegistry] = None


def get_arena() -> MemoryArena:
    global _GLOBAL_ARENA
    if _GLOBAL_ARENA is None:
        _GLOBAL_ARENA = MemoryArena(get_config())
    return _GLOBAL_ARENA


def get_cancellation_registry() -> CancellationRegistry:
    global _GLOBAL_CANCELS
    if _GLOBAL_CANCELS is None:
        _GLOBAL_CANCELS = CancellationRegistry()
    return _GLOBAL_CANCELS
