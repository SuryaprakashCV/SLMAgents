"""
Unified Pure-CPU Harness Runtime Facade (`HarnessRuntime`).

Mediates all LLM, Embedding, Reranker, Whisper STT, and Vision OCR/VLM requests through
the watermark `MemoryArena`, `CPUTopology` thread auto-tuner, and `CancellationRegistry`.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

import numpy as np

from ..config import MODEL_CATALOG, HarnessConfig, ModelSpec, get_config, set_active_profile
from .arena import (
    CancellationRegistry,
    CancellationToken,
    MemoryArena,
    RunCancelledError,
    get_arena,
    get_cancellation_registry,
)
from .constrained import (
    GrammarSpec,
    ONNXPrefixEnumMasker,
    extract_and_repair_json,
    strip_thinking_blocks,
)
from .cpu_topology import (
    CPUTopology,
    configure_cpu_environment,
    detect_cpu_topology,
    get_cpu_topology,
    get_process_rss_mb,
)
from .gguf_cpu import GGUFCPUBackend
from .multimodal_cpu import (
    CPUEmbeddingEngine,
    CPURerankerEngine,
    CPUVisionEngine,
    CPUWhisperEngine,
)
from .onnx_cpu import (
    GenerationChunk,
    GenerationRequest,
    GenerationResult,
    ONNXCPUBackend,
)


class HarnessRuntime:
    """Central CPU inference scheduler & model lifecycle manager."""

    def __init__(self, config: Optional[HarnessConfig] = None) -> None:
        self.config = config or get_config()
        self.topology = configure_cpu_environment(
            override_threads=self.config.n_threads,
            pure_cpu=self.config.pure_cpu,
        )
        self.arena = MemoryArena(self.config)
        self.cancellations = get_cancellation_registry()
        self._fast_tokenizer: Any = None
        self._tok_lock = threading.Lock()

    def switch_profile(self, profile_name: str, preferred_backend: Optional[str] = None) -> Dict[str, Any]:
        set_active_profile(profile_name, preferred_backend=preferred_backend)
        evicted = self.arena.purge_unpinned()
        return {
            "profile": self.config.profile.value,
            "preferred_backend": self.config.preferred_backend,
            "evicted_models": evicted,
            "status": self.arena.status(),
        }

    def _load_spec_instance(self, spec: ModelSpec) -> Any:
        if spec.backend == "gguf":
            return GGUFCPUBackend(spec, self.config)
        if spec.backend == "onnx":
            return ONNXCPUBackend(spec, self.config)
        if spec.backend == "onnx_embed":
            return CPUEmbeddingEngine(spec, self.config)
        if spec.backend == "onnx_rerank":
            return CPURerankerEngine(spec, self.config)
        if spec.backend == "onnx_whisper":
            return CPUWhisperEngine(spec, self.config)
        if spec.backend == "onnx_vision":
            return CPUVisionEngine(spec, self.config)
        raise ValueError(f"Unsupported backend '{spec.backend}' for {spec.model_id}")

    def get_llm(
        self,
        role: str = "primary",
        model_id: Optional[str] = None,
    ) -> Union[GGUFCPUBackend, ONNXCPUBackend]:
        if model_id and model_id in MODEL_CATALOG:
            spec = MODEL_CATALOG[model_id]
        else:
            spec = self.config.resolve_model_spec(role)

        if self.config.preferred_backend == "onnx" and spec.backend == "gguf":
            alt_id = self.config.role_bindings.get("onnx_primary", "qwen2.5-coder-3b-onnx")
            spec = MODEL_CATALOG.get(alt_id, MODEL_CATALOG["qwen3.5-0.8b-onnx"])
        elif self.config.preferred_backend == "gguf" and spec.backend == "onnx" and role in ("primary", "coding"):
            gguf_spec = MODEL_CATALOG["qwen3-4b-gguf"]
            if os.path.exists(gguf_spec.abs_path(self.config.models_dir)):
                spec = gguf_spec

        try:
            return self.arena.acquire(spec, self._load_spec_instance)
        except Exception:
            if spec.fallback_model_id and spec.fallback_model_id in MODEL_CATALOG:
                fb = MODEL_CATALOG[spec.fallback_model_id]
                return self.arena.acquire(fb, self._load_spec_instance)
            return self.arena.acquire(MODEL_CATALOG["qwen3.5-0.8b-onnx"], self._load_spec_instance)

    def stream(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        prompt: Optional[str] = None,
        role: str = "primary",
        model_id: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        top_p: float = 0.9,
        top_k: int = 40,
        repetition_penalty: float = 1.05,
        stop: Optional[List[str]] = None,
        grammar: Optional[GrammarSpec] = None,
        enable_thinking: bool = False,
        cancel_token: Optional[CancellationToken] = None,
    ) -> Iterator[GenerationChunk]:
        llm = self.get_llm(role=role, model_id=model_id)
        req = GenerationRequest(
            messages=messages,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
            stop=stop or [],
            grammar=grammar,
            enable_thinking=enable_thinking,
            cancel_token=cancel_token,
        )
        return llm.stream(req)

    def generate(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        prompt: Optional[str] = None,
        role: str = "primary",
        model_id: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        top_p: float = 0.9,
        top_k: int = 40,
        repetition_penalty: float = 1.05,
        stop: Optional[List[str]] = None,
        grammar: Optional[GrammarSpec] = None,
        enable_thinking: bool = False,
        cancel_token: Optional[CancellationToken] = None,
    ) -> GenerationResult:
        llm = self.get_llm(role=role, model_id=model_id)
        req = GenerationRequest(
            messages=messages,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            repetition_penalty=repetition_penalty,
            stop=stop or [],
            grammar=grammar,
            enable_thinking=enable_thinking,
            cancel_token=cancel_token,
        )
        return llm.generate(req)

    def get_embedder(self) -> CPUEmbeddingEngine:
        spec = self.config.resolve_model_spec("embeddings")
        return self.arena.acquire(spec, self._load_spec_instance)

    def embed_query(self, text: str) -> np.ndarray:
        return self.get_embedder().embed_query(text)

    def embed_batch(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        return self.get_embedder().embed_batch(texts, batch_size=batch_size)

    def get_reranker(self) -> CPURerankerEngine:
        spec = self.config.resolve_model_spec("reranker")
        return self.arena.acquire(spec, self._load_spec_instance)

    def rerank(
        self,
        query: str,
        passages: Sequence[str],
        top_k: int = 6,
    ) -> List[Tuple[int, float]]:
        """Returns `[(original_index, score)]` sorted descending by BGE INT8 cross-encoder score."""
        if not passages:
            return []
        try:
            engine = self.get_reranker()
            scores = engine.score_pairs(query, passages)
            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            return ranked[:top_k]
        except Exception:
            # Graceful dense cosine fallback if reranker weights are missing
            q_vec = self.embed_query(query)
            p_vecs = self.embed_batch(passages)
            sims = (p_vecs @ q_vec).tolist()
            ranked = sorted(enumerate(float(s) for s in sims), key=lambda x: x[1], reverse=True)
            return ranked[:top_k]

    def transcribe_audio(self, audio_path: str, language: str = "english") -> Optional[str]:
        spec = self.config.resolve_model_spec("stt")
        engine: CPUWhisperEngine = self.arena.acquire(spec, self._load_spec_instance)
        return engine.transcribe(audio_path, language=language)

    def analyze_image(
        self,
        image_path: str,
        prompt: str = "Describe this image clearly and accurately.",
        ocr_only: bool = False,
    ) -> Dict[str, Any]:
        spec = self.config.resolve_model_spec("vision")
        engine: CPUVisionEngine = self.arena.acquire(spec, self._load_spec_instance)
        return engine.analyze(image_path, prompt=prompt, ocr_only=ocr_only)

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        with self._tok_lock:
            if self._fast_tokenizer is None:
                try:
                    from tokenizers import Tokenizer

                    for cand in (
                        "qwen3.5-0.8b-onnx",
                        "qwen2.5-coder-3b-onnx",
                        "qwen2.5_coder_text2sql_onnx",
                        "all-minilm-l6-v2-onnx",
                    ):
                        tp = os.path.join(self.config.models_dir, cand, "tokenizer.json")
                        if os.path.isfile(tp):
                            self._fast_tokenizer = Tokenizer.from_file(tp)
                            break
                except Exception:
                    self._fast_tokenizer = False
            if self._fast_tokenizer and self._fast_tokenizer is not False:
                try:
                    return len(self._fast_tokenizer.encode(text).ids)
                except Exception:
                    pass
        return max(1, int(len(text) / 3.7))

    def status(self) -> Dict[str, Any]:
        return {
            "cpu": self.topology.to_dict(),
            "arena": self.arena.status(),
        }


_GLOBAL_RUNTIME: Optional[HarnessRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> HarnessRuntime:
    global _GLOBAL_RUNTIME
    if _GLOBAL_RUNTIME is None:
        with _RUNTIME_LOCK:
            if _GLOBAL_RUNTIME is None:
                _GLOBAL_RUNTIME = HarnessRuntime(get_config())
    return _GLOBAL_RUNTIME


__all__ = [
    "CPUEmbeddingEngine",
    "CPURerankerEngine",
    "CPUTopology",
    "CPUVisionEngine",
    "CPUWhisperEngine",
    "CancellationRegistry",
    "CancellationToken",
    "GGUFCPUBackend",
    "GenerationChunk",
    "GenerationRequest",
    "GenerationResult",
    "GrammarSpec",
    "HarnessRuntime",
    "MemoryArena",
    "ONNXCPUBackend",
    "ONNXPrefixEnumMasker",
    "RunCancelledError",
    "configure_cpu_environment",
    "detect_cpu_topology",
    "extract_and_repair_json",
    "get_arena",
    "get_cancellation_registry",
    "get_cpu_topology",
    "get_process_rss_mb",
    "get_runtime",
    "strip_thinking_blocks",
]
