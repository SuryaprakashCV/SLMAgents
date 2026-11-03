"""
Clean Zero-Circular-Import Compatibility Bridge for Standalone `slm_*` Specialist Packages.

Replaces the legacy `import main; main.get_shared_onnx_genai()` anti-pattern across all 26
`slm_*` packages so every package works standalone on pure CPU with real `temperature`/`top_p`
sampling, dynamic 2D/3D `position_ids`, and `MemoryArena` governance — without importing `main.py`!
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config import MODEL_CATALOG, ModelSpec, get_config
from . import get_runtime
from .onnx_cpu import DEFAULT_EOS_IDS, ONNXCPUBackend, sample_next_token

try:
    import onnxruntime_genai as _native_og
    _ORIG_OG_MODEL = getattr(_native_og, "_slm_orig_Model", _native_og.Model)
    _ORIG_OG_TOKENIZER = getattr(_native_og, "_slm_orig_Tokenizer", _native_og.Tokenizer)
    _ORIG_OG_PARAMS = getattr(_native_og, "_slm_orig_GeneratorParams", _native_og.GeneratorParams)
    _ORIG_OG_GENERATOR = getattr(_native_og, "_slm_orig_Generator", _native_og.Generator)
    _native_og._slm_orig_Model = _ORIG_OG_MODEL
    _native_og._slm_orig_Tokenizer = _ORIG_OG_TOKENIZER
    _native_og._slm_orig_GeneratorParams = _ORIG_OG_PARAMS
    _native_og._slm_orig_Generator = _ORIG_OG_GENERATOR
except ImportError:
    _native_og = None
    _ORIG_OG_MODEL = None
    _ORIG_OG_TOKENIZER = None
    _ORIG_OG_PARAMS = None
    _ORIG_OG_GENERATOR = None


class HarnessONNXModelHandle:
    """Wraps `ONNXCPUBackend` behind the lightweight handle expected by `slm_*` packages."""

    def __init__(self, backend: ONNXCPUBackend) -> None:
        self.backend = backend
        self.model_dir = backend.model_dir


class HarnessONNXTokenizerHandle:
    def __init__(self, backend: ONNXCPUBackend) -> None:
        self.backend = backend

    def encode(self, text: str) -> List[int]:
        return list(self.backend.tokenizer.encode(text).ids)

    def decode(self, token_ids: Any) -> str:
        if isinstance(token_ids, (int, np.integer)):
            ids = [int(token_ids)]
        elif hasattr(token_ids, "__iter__"):
            ids = [int(x) for x in token_ids]
        else:
            ids = [int(token_ids)]
        return self.backend.tokenizer.decode(ids)

    def create_stream(self) -> Any:
        tok = self

        class _Stream:
            def decode(self, token_id: int) -> str:
                return tok.decode([int(token_id)])

        return _Stream()


class HarnessGeneratorParams:
    def __init__(self, model: Any) -> None:
        self.model = model
        self.search_options: Dict[str, Any] = {
            "max_length": 2048,
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 40,
            "repetition_penalty": 1.05,
        }
        self.input_ids: Optional[List[int]] = None

    def set_search_options(self, **kwargs: Any) -> None:
        self.search_options.update(kwargs)


class HarnessSteppedGenerator:
    """
    Drop-in stepped generator supporting real `temperature`, `top_p`, `top_k`, `repetition_penalty`,
    and dynamic 0D/2D/3D `position_ids` via `ONNXCPUBackend`.
    """

    def __init__(self, model: HarnessONNXModelHandle, params: Optional[HarnessGeneratorParams] = None) -> None:
        self.backend = model.backend
        self.params = params or HarnessGeneratorParams(model)
        self.tokens_history: List[int] = []
        self.state: Dict[str, np.ndarray] = self.backend._init_empty_state()
        self.last_logits: Optional[np.ndarray] = None
        self.next_tokens: List[int] = []
        self.step = 0
        self.max_new_tokens = 1024
        self.done = False
        self._rng = np.random.default_rng()
        if self.params.input_ids:
            self.append_tokens(self.params.input_ids)

    def append_tokens(self, tokens: Sequence[int]) -> None:
        tok_list = [int(x) for x in tokens]
        if not tok_list:
            return
        past_len = len(self.tokens_history)
        self.tokens_history.extend(tok_list)

        max_len = int(self.params.search_options.get("max_length", len(self.tokens_history) + 512))
        cap = int(os.environ.get("SLM_MAX_GENERATED_TOKENS", 1200))
        self.max_new_tokens = max(1, min(cap, max_len - len(self.tokens_history)))

        chunk_sz = 256
        cur = 0
        with self.backend._lock:
            while cur < len(tok_list):
                sub = tok_list[cur : cur + chunk_sz]
                self.last_logits, self.state = self.backend._run_forward_slice(sub, past_len + cur, self.state)
                cur += len(sub)

    def compute_logits(self) -> None:
        # Logits are computed eagerly in `append_tokens` / `generate_next_token`
        return None

    def is_done(self) -> bool:
        return self.done

    def generate_next_token(self) -> None:
        if self.done or self.last_logits is None:
            self.done = True
            return
        if self.step >= self.max_new_tokens:
            self.done = True
            self.next_tokens = []
            return

        if self.step > 0 and self.next_tokens:
            prev_tok = self.next_tokens[0]
            past_len = len(self.tokens_history)
            self.tokens_history.append(prev_tok)
            with self.backend._lock:
                self.last_logits, self.state = self.backend._run_forward_slice([prev_tok], past_len, self.state)

        opts = self.params.search_options
        do_sample = opts.get("do_sample", True)
        temp = 0.0 if do_sample is False else float(opts.get("temperature", 0.2))
        tok = sample_next_token(
            self.last_logits[0, -1, :],
            self.tokens_history[-96:],
            temperature=temp,
            top_k=int(opts.get("top_k", 40)),
            top_p=float(opts.get("top_p", 0.9)),
            repetition_penalty=float(opts.get("repetition_penalty", 1.05)),
            rng=self._rng,
        )

        if tok in self.backend.eos_token_ids or tok in DEFAULT_EOS_IDS:
            self.done = True
            self.next_tokens = []
            return

        piece = self.backend.tokenizer.decode([tok])
        if any(stop_tag in piece for stop_tag in ("<|im_end|>", "<|endoftext|>", "<|im_start|>")):
            self.done = True
            self.next_tokens = []
            return

        self.next_tokens = [tok]
        self.step += 1

    def get_next_tokens(self) -> List[int]:
        return self.next_tokens

    def get_sequence(self, index: int = 0) -> List[int]:
        return list(self.tokens_history) + list(self.next_tokens)


def _load_onnx_dir_via_harness(model_dir: str) -> HarnessONNXModelHandle:
    abspath = os.path.abspath(model_dir)
    rt = get_runtime()
    for spec in MODEL_CATALOG.values():
        if spec.backend == "onnx" and os.path.abspath(spec.abs_path(rt.config.models_dir)) == abspath:
            if rt._is_model_on_disk(spec):
                backend = rt.arena.acquire(spec, rt._load_spec_instance)
                return HarnessONNXModelHandle(backend)
            break

    onnx_sub = os.path.join(abspath, "onnx")
    has_local_onnx = (
        os.path.isdir(onnx_sub) and any(f.endswith(".onnx") for f in os.listdir(onnx_sub))
    ) or (
        os.path.isdir(abspath) and any(f.endswith(".onnx") for f in os.listdir(abspath))
    )
    if has_local_onnx:
        adhoc_spec = ModelSpec(
            role="adhoc_onnx",
            model_id=f"adhoc-{os.path.basename(abspath)}",
            backend="onnx",
            rel_path=abspath,
            est_ram_mb=1400,
        )
        backend = rt.arena.acquire(adhoc_spec, rt._load_spec_instance)
        return HarnessONNXModelHandle(backend)

    fallback_spec = rt.ensure_capability_model("coding")
    if fallback_spec.backend != "onnx":
        fallback_spec = MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"]
    backend = rt.arena.acquire(fallback_spec, rt._load_spec_instance)
    return HarnessONNXModelHandle(backend)


def install_smart_og_dispatch() -> None:
    """
    Installs a non-destructive smart dispatcher on `onnxruntime_genai`:
    - Native `genai_config.json` bundles use native `_ORIG_OG_*` C++ classes untouched!
    - Standard Optimum ONNX directories (`model_q4.onnx`, `decoder_model_merged_quantized.onnx`)
      seamlessly use `HarnessONNXModelHandle` + `HarnessSteppedGenerator`!
    """
    if _native_og is None or getattr(_native_og, "_slm_smart_dispatch_installed", False):
        return

    def _smart_model(model_dir: str) -> Any:
        if os.path.isfile(os.path.join(model_dir, "genai_config.json")) and _ORIG_OG_MODEL is not None:
            return _ORIG_OG_MODEL(model_dir)
        return _load_onnx_dir_via_harness(model_dir)

    def _smart_tokenizer(model: Any) -> Any:
        if isinstance(model, HarnessONNXModelHandle):
            return HarnessONNXTokenizerHandle(model.backend)
        return _ORIG_OG_TOKENIZER(model)

    def _smart_params(model: Any) -> Any:
        if isinstance(model, HarnessONNXModelHandle):
            return HarnessGeneratorParams(model)
        return _ORIG_OG_PARAMS(model)

    def _smart_generator(model: Any, params: Any) -> Any:
        if isinstance(model, HarnessONNXModelHandle):
            return HarnessSteppedGenerator(model, params)
        return _ORIG_OG_GENERATOR(model, params)

    _native_og.Model = _smart_model
    _native_og.Tokenizer = _smart_tokenizer
    _native_og.GeneratorParams = _smart_params
    _native_og.Generator = _smart_generator
    _native_og._slm_smart_dispatch_installed = True


def get_shared_genai(role: str = "onnx_primary") -> Tuple[HarnessONNXModelHandle, HarnessONNXTokenizerHandle]:
    """
    Returns `(model_handle, tokenizer_handle)` managed by `HarnessRuntime` & `MemoryArena`
    without importing `main.py`!
    """
    install_smart_og_dispatch()
    rt = get_runtime()
    cfg = get_config()
    onnx_id = cfg.role_bindings.get(role) or cfg.role_bindings.get("onnx_primary") or "qwen2.5-coder-1.5b-onnx"
    spec = MODEL_CATALOG.get(onnx_id, MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"])
    if spec.backend != "onnx" or not rt._is_model_on_disk(spec):
        for cand_id in ("qwen2.5-coder-3b-onnx", "qwen2.5-coder-1.5b-onnx", "qwen3.5-0.8b-onnx"):
            cand = MODEL_CATALOG.get(cand_id)
            if cand and rt._is_model_on_disk(cand):
                spec = cand
                break
        else:
            spec = MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"]
    backend: ONNXCPUBackend = rt.arena.acquire(spec, rt._load_spec_instance)
    return HarnessONNXModelHandle(backend), HarnessONNXTokenizerHandle(backend)
