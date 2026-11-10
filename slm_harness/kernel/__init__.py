"""
Plane 1: Event-Driven Agentic Kernel, Checkpoints, Shadow Snapshot VFS & Multi-Mode State Machine.
"""

from typing import TYPE_CHECKING, Any

from .checkpoints import (
    CheckpointStore,
    ShadowSnapshotVFS,
    SteerQueue,
    TurnNode,
    get_checkpoint_store,
    get_shadow_vfs,
    get_steer_queue,
)
from .events import EventBus, EventType, HarnessEvent, get_event_bus

if TYPE_CHECKING:
    from .state_machine import TurnOutcome, TurnStateMachine, get_kernel


def __getattr__(name: str) -> Any:
    if name in ("TurnOutcome", "TurnStateMachine", "get_kernel"):
        from . import state_machine

        return getattr(state_machine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CheckpointStore",
    "EventBus",
    "EventType",
    "HarnessEvent",
    "ShadowSnapshotVFS",
    "SteerQueue",
    "TurnNode",
    "TurnOutcome",
    "TurnStateMachine",
    "get_checkpoint_store",
    "get_event_bus",
    "get_kernel",
    "get_shadow_vfs",
    "get_steer_queue",
]
