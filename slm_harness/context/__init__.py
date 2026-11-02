"""
Plane 3: Prefix-Cached Context Compiler, Content-Addressed Artifact Offloading & Episodic Compactor.
"""

from .artifacts import ArtifactEntry, ArtifactStore, get_artifact_store
from .compactor import compact_session_history
from .compiler import CompiledContext, ContextCompiler, get_context_compiler

__all__ = [
    "ArtifactEntry",
    "ArtifactStore",
    "CompiledContext",
    "ContextCompiler",
    "compact_session_history",
    "get_artifact_store",
    "get_context_compiler",
]
