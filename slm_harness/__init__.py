"""
SLMAgents 10-Plane Pure-CPU Interactive Agent Harness.

Provides an event-driven agentic kernel, grammar-constrained multi-backend CPU runtime
(ONNX Runtime CPUExecutionProvider + llama.cpp pure CPU n_gpu_layers=0), prefix-cached
context compiler, 4-tier cognitive memory, deterministic verifiers, capability sandbox,
human-in-the-loop brokers, and 4 unified user/developer surfaces.
"""

__version__ = "1.0.0"

from .config import HarnessConfig, CPUProfile, get_config, set_active_profile

__all__ = [
    "__version__",
    "HarnessConfig",
    "CPUProfile",
    "get_config",
    "set_active_profile",
]
