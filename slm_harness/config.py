"""
3-Tier Configuration Cascade, CPU Hardware Profiles & Logical Model Role Registry.

Precedence:
  1. Runtime / Session / CLI overrides & environment variables (`SLM_*`)
  2. Workspace configuration (`.slm/config.yaml` + `SLM.md` / `.slm/rules/*.md`)
  3. Global user configuration (`~/.slm/config.yaml`)
  4. Built-in hardware-calibrated defaults (`lite`, `balanced`, `power`)
"""

from __future__ import annotations

import os
import glob
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None


import sys

REPO_ROOT = (
    os.path.abspath(getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), "..")))
    if getattr(sys, "frozen", False)
    else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)
USER_DATA_DIR = os.path.expanduser("~/.slm_harness")
USER_MODELS_DIR = os.path.join(USER_DATA_DIR, "models")
REPO_MODELS_DIR = os.path.join(REPO_ROOT, "models")
DEFAULT_MODELS_DIR = REPO_MODELS_DIR if os.path.isdir(REPO_MODELS_DIR) else USER_MODELS_DIR


class CPUProfile(str, Enum):
    LITE = "lite"          # <= 2.5 GB RAM ceiling (0.8B ONNX + MiniLM + RapidOCR)
    BALANCED = "balanced"  # <= 4.5 GB RAM ceiling (Qwen3-4B GGUF / Qwen2.5-Coder-3B ONNX + mxbai + BGE INT8 + SQL + Whisper)
    POWER = "power"        # <= 8.0 GB RAM ceiling (Qwen3-8B GGUF + 0.8B draft + full specialist suite)


class ReasoningEffort(str, Enum):
    LOW = "low"        # Fast sub-second TTFT (0.8B / 1.5B ONNX, direct answers, minimal overhead)
    MEDIUM = "medium"  # Balanced (3B ONNX / 4B GGUF, structured upfront plan + sandbox verification)
    HIGH = "high"      # Deep reasoning (4B / 8B GGUF fitting RAM, web research, detailed plan + self-review critic)



class ApprovalPolicy(str, Enum):
    AUTO_SAFE = "auto-safe"  # Auto-allow L0-L2 (pure, workspace read, sandboxed compute); prompt on L3-L4 (writes, shell, net)
    ASK_ALL = "ask"          # Prompt on L2-L4 (including sandbox Python/SQL exec)
    AUTONOMOUS = "auto"      # Auto-allow L0-L3 inside workspace; prompt only on L4 destructive/external shell
    STRICT_READONLY = "strict"  # Block L3-L4 mutations completely


@dataclass
class ModelSpec:
    role: str
    model_id: str
    backend: str  # "gguf" | "onnx" | "onnx_embed" | "onnx_rerank" | "onnx_whisper" | "onnx_vision"
    rel_path: str
    est_ram_mb: int
    context_length: int = 4096
    quantization: str = "int4"
    pinned: bool = False
    fallback_model_id: Optional[str] = None

    def abs_path(self, models_dir: str = DEFAULT_MODELS_DIR) -> str:
        if os.path.isabs(self.rel_path):
            return self.rel_path
        return os.path.join(models_dir, self.rel_path)


# Canonical CPU Model Catalog mapped directly to verified quantized artifacts on disk
MODEL_CATALOG: Dict[str, ModelSpec] = {
    "qwen3-8b-gguf": ModelSpec(
        role="general_reasoning",
        model_id="qwen3-8b-gguf",
        backend="gguf",
        rel_path=os.path.join("gguf", "Qwen_Qwen3-8B-Q4_K_M.gguf"),
        est_ram_mb=5100,
        context_length=8192,
        quantization="Q4_K_M",
        fallback_model_id="qwen3-4b-gguf",
    ),
    "qwen3-4b-gguf": ModelSpec(
        role="general_reasoning",
        model_id="qwen3-4b-gguf",
        backend="gguf",
        rel_path=os.path.join("gguf", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
        est_ram_mb=2600,
        context_length=8192,
        quantization="Q4_K_M",
        fallback_model_id="qwen2.5-coder-3b-onnx",
    ),
    "qwen2.5-coder-3b-onnx": ModelSpec(
        role="coding",
        model_id="qwen2.5-coder-3b-onnx",
        backend="onnx",
        rel_path="qwen2.5-coder-3b-onnx",
        est_ram_mb=2200,
        context_length=4096,
        quantization="int4",
        fallback_model_id="qwen2.5-coder-1.5b-onnx",
    ),
    "qwen2.5-coder-1.5b-onnx": ModelSpec(
        role="technical_fast",
        model_id="qwen2.5-coder-1.5b-onnx",
        backend="onnx",
        rel_path="qwen2.5-coder-1.5b-onnx",
        est_ram_mb=1350,
        context_length=4096,
        quantization="int4",
        fallback_model_id="qwen3.5-0.8b-onnx",
    ),
    "qwen2.5-1.5b-onnx": ModelSpec(
        role="multilingual_doc",
        model_id="qwen2.5-1.5b-onnx",
        backend="onnx",
        rel_path="qwen2.5-1.5b-onnx",
        est_ram_mb=1300,
        context_length=4096,
        quantization="int4",
        fallback_model_id="qwen2.5-coder-1.5b-onnx",
    ),
    "qwen2.5-math-1.5b-onnx": ModelSpec(
        role="math",
        model_id="qwen2.5-math-1.5b-onnx",
        backend="onnx",
        rel_path="qwen2.5-math-1.5b-onnx",
        est_ram_mb=1300,
        context_length=4096,
        quantization="int4",
        fallback_model_id="qwen2.5-coder-1.5b-onnx",
    ),
    "qwen2.5-text2sql-onnx": ModelSpec(
        role="sql",
        model_id="qwen2.5-text2sql-onnx",
        backend="onnx",
        rel_path="qwen2.5_coder_text2sql_onnx",
        est_ram_mb=850,
        context_length=4096,
        quantization="int4",
        fallback_model_id="qwen2.5-coder-1.5b-onnx",
    ),
    "qwen3.5-0.8b-onnx": ModelSpec(
        role="router_fast",
        model_id="qwen3.5-0.8b-onnx",
        backend="onnx",
        rel_path="qwen3.5-0.8b-onnx",
        est_ram_mb=920,
        context_length=4096,
        quantization="int4",
        fallback_model_id="qwen2.5-coder-1.5b-onnx",
    ),
    "mxbai-embed-large-onnx": ModelSpec(
        role="embeddings",
        model_id="mxbai-embed-large-onnx",
        backend="onnx_embed",
        rel_path="mxbai-embed-large-v1-onnx",
        est_ram_mb=330,
        context_length=512,
        quantization="int8",
        pinned=True,
        fallback_model_id="minilm-embed-onnx",
    ),
    "minilm-embed-onnx": ModelSpec(
        role="embeddings",
        model_id="minilm-embed-onnx",
        backend="onnx_embed",
        rel_path="all-minilm-l6-v2-onnx",
        est_ram_mb=65,
        context_length=512,
        quantization="int4",
        pinned=True,
    ),
    "bge-reranker-int8-onnx": ModelSpec(
        role="reranker",
        model_id="bge-reranker-int8-onnx",
        backend="onnx_rerank",
        rel_path="bge-reranker-base-onnx",
        est_ram_mb=275,
        context_length=512,
        quantization="int8",
        pinned=False,
    ),
    "whisper-small-int8-onnx": ModelSpec(
        role="stt",
        model_id="whisper-small-int8-onnx",
        backend="onnx_whisper",
        rel_path="whisper-small-onnx",
        est_ram_mb=310,
        context_length=448,
        quantization="int8",
    ),
    "qwen2-vl-2b-onnx": ModelSpec(
        role="vision",
        model_id="qwen2-vl-2b-onnx",
        backend="onnx_vision",
        rel_path="qwen2-vl-2b-onnx",
        est_ram_mb=1850,
        context_length=2048,
        quantization="int4",
    ),
}


PROFILE_PRESETS: Dict[CPUProfile, Dict[str, Any]] = {
    CPUProfile.LITE: {
        "ram_soft_limit_mb": 2400,
        "ram_hard_limit_mb": 3200,
        "context_window": 3072,
        "speculative_lookup": True,
        "prefill_chunk_size": 256,
        "role_bindings": {
            "primary": "qwen2.5-coder-1.5b-onnx",
            "router": "qwen2.5-coder-1.5b-onnx",
            "coding": "qwen2.5-coder-1.5b-onnx",
            "sql": "qwen2.5-coder-1.5b-onnx",
            "math": "qwen2.5-coder-1.5b-onnx",
            "embeddings": "minilm-embed-onnx",
            "reranker": "bge-reranker-int8-onnx",
            "stt": "whisper-small-int8-onnx",
            "vision": "qwen2-vl-2b-onnx",
        },
    },
    CPUProfile.BALANCED: {
        "ram_soft_limit_mb": 3900,
        "ram_hard_limit_mb": 4700,
        "context_window": 6144,
        "speculative_lookup": True,
        "prefill_chunk_size": 256,
        "role_bindings": {
            "primary": "qwen3-4b-gguf",
            "router": "qwen3.5-0.8b-onnx",
            "coding": "qwen3-4b-gguf",
            "onnx_primary": "qwen2.5-coder-3b-onnx",
            "sql": "qwen2.5-text2sql-onnx",
            "math": "qwen3-4b-gguf",
            "embeddings": "mxbai-embed-large-onnx",
            "reranker": "bge-reranker-int8-onnx",
            "stt": "whisper-small-int8-onnx",
            "vision": "qwen2-vl-2b-onnx",
        },
    },
    CPUProfile.POWER: {
        "ram_soft_limit_mb": 6800,
        "ram_hard_limit_mb": 8200,
        "context_window": 8192,
        "speculative_lookup": True,
        "prefill_chunk_size": 512,
        "role_bindings": {
            "primary": "qwen3-8b-gguf",
            "router": "qwen3.5-0.8b-onnx",
            "coding": "qwen3-8b-gguf",
            "onnx_primary": "qwen2.5-coder-3b-onnx",
            "sql": "qwen2.5-text2sql-onnx",
            "math": "qwen2.5-math-1.5b-onnx",
            "embeddings": "mxbai-embed-large-onnx",
            "reranker": "bge-reranker-int8-onnx",
            "stt": "whisper-small-int8-onnx",
            "vision": "qwen2-vl-2b-onnx",
        },
    },
}


@dataclass
class HarnessConfig:
    profile: CPUProfile = CPUProfile.BALANCED
    pure_cpu: bool = True  # Enforce CPUExecutionProvider and n_gpu_layers=0
    preferred_backend: str = "auto"  # "auto" | "gguf" | "onnx"
    workspace_dir: str = field(default_factory=lambda: os.getcwd())
    models_dir: str = DEFAULT_MODELS_DIR
    data_dir: str = field(default_factory=lambda: os.path.expanduser("~/.slm_harness"))
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO_SAFE
    max_react_steps: int = 6
    max_verifier_retries: int = 2
    n_threads: Optional[int] = None
    ram_soft_limit_mb: int = 3900
    ram_hard_limit_mb: int = 4700
    context_window: int = 6144
    prefill_chunk_size: int = 256
    speculative_lookup: bool = True
    offline_only: bool = False
    role_bindings: Dict[str, str] = field(default_factory=dict)
    hooks: Dict[str, List[str]] = field(default_factory=dict)
    mcp_servers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    workspace_instructions: str = ""
    default_reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM

    def apply_profile(self, profile: CPUProfile) -> None:
        self.profile = profile
        preset = PROFILE_PRESETS[profile]
        self.ram_soft_limit_mb = int(preset["ram_soft_limit_mb"])
        self.ram_hard_limit_mb = int(preset["ram_hard_limit_mb"])
        self.context_window = int(preset["context_window"])
        self.prefill_chunk_size = int(preset["prefill_chunk_size"])
        self.speculative_lookup = bool(preset["speculative_lookup"])
        self.role_bindings = dict(preset["role_bindings"])
        if self.preferred_backend == "onnx":
            if self.role_bindings.get("primary", "").endswith("-gguf"):
                self.role_bindings["primary"] = (
                    "qwen2.5-coder-3b-onnx"
                    if profile != CPUProfile.LITE
                    else "qwen3.5-0.8b-onnx"
                )
                self.role_bindings["coding"] = self.role_bindings["primary"]

    def preferred_candidates_for_reasoning(
        self,
        role: str,
        reasoning_effort: str = "medium",
        available_ram_mb: int = 4000,
    ) -> List[str]:
        """Returns ordered candidate model IDs calibrated to (role, reasoning_effort, available_ram_mb)."""
        effort = (reasoning_effort or self.default_reasoning_effort.value or "medium").lower().strip()
        if role in ("embeddings", "reranker", "stt", "vision", "router"):
            base_id = self.role_bindings.get(role, "qwen3.5-0.8b-onnx")
            return [base_id]

        if role == "sql":
            if effort == "high":
                return ["qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen2.5-coder-1.5b-onnx", "qwen2.5-text2sql-onnx"]
            if effort == "low":
                return ["qwen2.5-coder-1.5b-onnx", "qwen2.5-text2sql-onnx", "qwen2.5-coder-3b-onnx"]
            return ["qwen2.5-coder-3b-onnx", "qwen3-4b-gguf", "qwen2.5-coder-1.5b-onnx", "qwen2.5-text2sql-onnx"]

        if role == "math":
            if effort == "low":
                return ["qwen2.5-math-1.5b-onnx", "qwen2.5-coder-1.5b-onnx", "qwen3.5-0.8b-onnx"]
            if effort == "high":
                return ["qwen3-4b-gguf", "qwen2.5-math-1.5b-onnx", "qwen2.5-coder-3b-onnx"]
            return ["qwen2.5-math-1.5b-onnx", "qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen3.5-0.8b-onnx"]

        if role == "coding":
            if effort == "low":
                return ["qwen2.5-coder-1.5b-onnx", "qwen2.5-coder-3b-onnx", "qwen3-4b-gguf", "qwen3.5-0.8b-onnx"]
            if effort == "high":
                if available_ram_mb >= 5400:
                    return ["qwen3-8b-gguf", "qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen2.5-coder-1.5b-onnx"]
                return ["qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen2.5-coder-1.5b-onnx"]
            # medium
            return ["qwen2.5-coder-3b-onnx", "qwen3-4b-gguf", "qwen2.5-coder-1.5b-onnx", "qwen3.5-0.8b-onnx"]

        # primary / general
        if effort == "low":
            return ["qwen2.5-coder-1.5b-onnx", "qwen2.5-1.5b-onnx", "qwen2.5-coder-3b-onnx", "qwen3-4b-gguf", "qwen3.5-0.8b-onnx"]
        if effort == "high":
            if available_ram_mb >= 5400:
                return ["qwen3-8b-gguf", "qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen2.5-1.5b-onnx"]
            return ["qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen2.5-1.5b-onnx", "qwen3.5-0.8b-onnx"]
        # medium
        return ["qwen3-4b-gguf", "qwen2.5-coder-3b-onnx", "qwen2.5-1.5b-onnx", "qwen3.5-0.8b-onnx"]

    @staticmethod
    def _has_weights_at(spec: ModelSpec, base_dir: str) -> bool:
        target = spec.abs_path(base_dir)
        if spec.backend == "gguf":
            return os.path.isfile(target) and os.path.getsize(target) > 5 * 1024 * 1024
        onnx_sub = os.path.join(target, "onnx")
        if os.path.isdir(onnx_sub) and any(f.endswith(".onnx") for f in os.listdir(onnx_sub)):
            return True
        return os.path.isdir(target) and any(f.endswith(".onnx") for f in os.listdir(target))

    def resolve_model_for_reasoning(
        self,
        role: str,
        reasoning_effort: str = "medium",
        available_ram_mb: int = 4000,
    ) -> ModelSpec:
        candidates = self.preferred_candidates_for_reasoning(role, reasoning_effort, available_ram_mb)
        for cand_id in candidates:
            spec = MODEL_CATALOG.get(cand_id)
            if not spec:
                continue
            if spec.est_ram_mb <= max(1200, available_ram_mb + 600):
                if self._has_weights_at(spec, self.models_dir):
                    return spec
                if self._has_weights_at(spec, USER_MODELS_DIR):
                    self.models_dir = USER_MODELS_DIR
                    return spec
        return self.resolve_model_spec(role)

    def resolve_model_spec(self, role: str) -> ModelSpec:
        model_id = self.role_bindings.get(role) or self.role_bindings.get("primary", "qwen2.5-coder-1.5b-onnx")
        spec = MODEL_CATALOG.get(model_id)
        if spec is None:
            spec = MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"]

        # Walk fallback chain automatically if model weight file isn't present on disk
        visited = set()
        while spec and spec.model_id not in visited:
            visited.add(spec.model_id)
            if self._has_weights_at(spec, self.models_dir):
                return spec
            if self._has_weights_at(spec, USER_MODELS_DIR):
                self.models_dir = USER_MODELS_DIR
                return spec
            if spec.fallback_model_id and spec.fallback_model_id in MODEL_CATALOG:
                spec = MODEL_CATALOG[spec.fallback_model_id]
            else:
                break
        return spec

    def load_workspace_rules(self) -> str:
        """Discovers SLM.md, AGENTS.md, and .slm/rules/*.md in workspace_dir."""
        rules_parts: List[str] = []
        candidates = [
            os.path.join(self.workspace_dir, "SLM.md"),
            os.path.join(self.workspace_dir, ".slm", "SLM.md"),
        ]
        rules_glob = sorted(glob.glob(os.path.join(self.workspace_dir, ".slm", "rules", "*.md")))
        for path in candidates + rules_glob:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().strip()
                    if content:
                        rel = os.path.relpath(path, self.workspace_dir)
                        rules_parts.append(f"### Workspace Rules ({rel})\n{content[:4000]}")
                except Exception:
                    pass
        self.workspace_instructions = "\n\n".join(rules_parts)
        return self.workspace_instructions


def _load_yaml_file(path: str) -> Dict[str, Any]:
    if not yaml or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_json_file(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_config(
    workspace_dir: Optional[str] = None,
    profile: Optional[str] = None,
    preferred_backend: Optional[str] = None,
) -> HarnessConfig:
    ws = os.path.abspath(workspace_dir or os.environ.get("SLM_WORKSPACE", os.getcwd()))
    global_cfg = _load_yaml_file(os.path.expanduser("~/.slm/config.yaml"))
    ws_cfg = _load_yaml_file(os.path.join(ws, ".slm", "config.yaml"))
    merged = {**global_cfg, **ws_cfg}

    raw_prof = (
        profile
        or os.environ.get("SLM_PROFILE")
        or merged.get("profile")
        or CPUProfile.BALANCED.value
    ).lower()
    try:
        prof_enum = CPUProfile(raw_prof)
    except ValueError:
        prof_enum = CPUProfile.BALANCED

    cfg = HarnessConfig(workspace_dir=ws)
    cfg.preferred_backend = (
        preferred_backend
        or os.environ.get("SLM_BACKEND")
        or merged.get("preferred_backend")
        or "auto"
    ).lower()
    cfg.pure_cpu = os.environ.get("SLM_PURE_CPU", str(merged.get("pure_cpu", "1"))).lower() not in ("0", "false", "no")
    cfg.offline_only = os.environ.get("SLM_OFFLINE", str(merged.get("offline_only", "0"))).lower() in ("1", "true", "yes")

    raw_policy = (os.environ.get("SLM_APPROVAL_POLICY") or merged.get("approval_policy") or "auto-safe").lower()
    try:
        cfg.approval_policy = ApprovalPolicy(raw_policy)
    except ValueError:
        cfg.approval_policy = ApprovalPolicy.AUTO_SAFE

    if os.environ.get("SLM_MODELS_DIR"):
        cfg.models_dir = os.path.abspath(os.environ["SLM_MODELS_DIR"])
    elif merged.get("models_dir"):
        cfg.models_dir = os.path.abspath(os.path.expanduser(merged["models_dir"]))

    if os.environ.get("SLM_DATA_DIR"):
        cfg.data_dir = os.path.abspath(os.path.expanduser(os.environ["SLM_DATA_DIR"]))

    if os.environ.get("SLM_N_THREADS"):
        try:
            cfg.n_threads = max(1, int(os.environ["SLM_N_THREADS"]))
        except ValueError:
            pass

    cfg.apply_profile(prof_enum)

    # Custom role overrides from config files
    if isinstance(merged.get("role_bindings"), dict):
        cfg.role_bindings.update(merged["role_bindings"])
    if isinstance(merged.get("hooks"), dict):
        cfg.hooks.update(merged["hooks"])

    # Load MCP servers from ~/.slm/mcp.json and .slm/mcp.json
    for mcp_path in [
        os.path.expanduser("~/.slm/mcp.json"),
        os.path.join(ws, ".slm", "mcp.json"),
    ]:
        mcp_data = _load_json_file(mcp_path)
        servers = mcp_data.get("mcpServers") or mcp_data.get("servers") or {}
        if isinstance(servers, dict):
            cfg.mcp_servers.update(servers)

    os.makedirs(cfg.data_dir, exist_ok=True)
    cfg.load_workspace_rules()
    return cfg


_GLOBAL_CONFIG: Optional[HarnessConfig] = None


def get_config() -> HarnessConfig:
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = build_config()
    return _GLOBAL_CONFIG


def set_active_profile(profile_name: str, preferred_backend: Optional[str] = None) -> HarnessConfig:
    cfg = get_config()
    if preferred_backend:
        cfg.preferred_backend = preferred_backend.lower()
    try:
        prof = CPUProfile(profile_name.lower())
    except ValueError:
        prof = CPUProfile.BALANCED
    cfg.apply_profile(prof)
    return cfg
