"""
Unified Pure-CPU Harness Runtime Facade (`HarnessRuntime`).

Mediates all LLM, Embedding, Reranker, Whisper STT, and Vision OCR/VLM requests through
the watermark `MemoryArena`, `CPUTopology` thread auto-tuner, and `CancellationRegistry`.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, Union

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

    def _is_model_on_disk(self, spec: ModelSpec) -> bool:
        target_path = spec.abs_path(self.config.models_dir)
        if spec.backend == "gguf":
            return os.path.isfile(target_path)
        onnx_sub = os.path.join(target_path, "onnx")
        return (
            os.path.isdir(onnx_sub) and any(f.endswith(".onnx") for f in os.listdir(onnx_sub))
        ) or (
            os.path.isdir(target_path) and any(f.endswith(".onnx") for f in os.listdir(target_path))
        )

    def _ensure_model_downloaded(
        self,
        spec: ModelSpec,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        if self._is_model_on_disk(spec):
            return True
        if self.config.offline_only:
            return False

        target_path = spec.abs_path(self.config.models_dir)
        topo = detect_cpu_topology(override_threads=self.config.n_threads)
        # Verify model fits available RAM + swap headroom before downloading
        if topo.available_ram_mb > 0 and spec.est_ram_mb > (topo.available_ram_mb + 2048):
            return False

        try:
            from huggingface_hub import hf_hub_download, snapshot_download
            import download_models

            for item in download_models.AGENT_MODELS:
                if spec.backend == "gguf" and item.get("kind") == "gguf_file":
                    rel_match = os.path.join(item["dir"], item["filename"])
                    if os.path.normpath(spec.rel_path) == os.path.normpath(rel_match):
                        dest_dir = os.path.dirname(target_path)
                        os.makedirs(dest_dir, exist_ok=True)
                        msg = (
                            f"Auto-downloading '{spec.model_id}' ({spec.est_ram_mb} MB) "
                            f"calibrated for {topo.available_ram_mb} MB available RAM & {topo.recommended_threads} CPU threads..."
                        )
                        print(f"[HarnessRuntime] {msg}")
                        if progress_callback:
                            progress_callback(msg)
                        hf_hub_download(repo_id=item["repo"], filename=item["filename"], local_dir=dest_dir)
                        return self._is_model_on_disk(spec)
                elif item.get("dir") == spec.rel_path or os.path.basename(target_path) == item.get("dir"):
                    os.makedirs(target_path, exist_ok=True)
                    msg = (
                        f"Auto-downloading '{spec.model_id}' ({spec.est_ram_mb} MB) "
                        f"calibrated for {topo.available_ram_mb} MB available RAM & {topo.recommended_threads} CPU threads..."
                    )
                    print(f"[HarnessRuntime] {msg}")
                    if progress_callback:
                        progress_callback(msg)
                    snapshot_download(
                        repo_id=item["repo"],
                        local_dir=target_path,
                        allow_patterns=item.get("allow_patterns"),
                    )
                    return self._is_model_on_disk(spec)
        except Exception as exc:
            print(f"[HarnessRuntime] On-demand model download note for {spec.model_id}: {exc}")
        return self._is_model_on_disk(spec)

    def ensure_capability_model(
        self,
        role: str = "primary",
        reasoning_effort: Optional[str] = None,
        model_id: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ModelSpec:
        """
        Selects the optimal CPU model for (role, reasoning_effort) that fits available RAM & CPU.
        If the required model for a specialist capability is not yet present on disk, downloads it on-demand.
        """
        self.arena.check_memory_pressure()
        topo = self.topology
        avail_mb = topo.available_ram_mb if topo.available_ram_mb > 0 else 4000

        if model_id and model_id in MODEL_CATALOG:
            explicit_spec = MODEL_CATALOG[model_id]
            if self._is_model_on_disk(explicit_spec) or self._ensure_model_downloaded(explicit_spec, progress_callback):
                return explicit_spec

        effort = (reasoning_effort or self.config.default_reasoning_effort.value or "medium").lower().strip()
        is_hosted_2vcpu = bool(os.environ.get("SPACE_ID") or self.config.profile.value == "lite" or topo.physical_cores <= 2)
        if is_hosted_2vcpu:
            if role in ("embeddings", "reranker", "stt"):
                spec = self.config.resolve_model_spec(role)
                if not self._is_model_on_disk(spec):
                    self._ensure_model_downloaded(spec, progress_callback=progress_callback)
                return spec
            fast_spec = MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"]
            if self._is_model_on_disk(fast_spec) or self._ensure_model_downloaded(fast_spec, progress_callback=progress_callback):
                return fast_spec
            fb_spec = MODEL_CATALOG["qwen3.5-0.8b-onnx"]
            self._ensure_model_downloaded(fb_spec, progress_callback=progress_callback)
            return fb_spec

        candidates = self.config.preferred_candidates_for_reasoning(role, effort, avail_mb)

        # 1. Prefer an already-downloaded candidate in the reasoning tier priority list that fits RAM
        for cand_id in candidates:
            spec = MODEL_CATALOG.get(cand_id)
            if spec and spec.est_ram_mb <= max(1200, avail_mb + 1400) and self._is_model_on_disk(spec):
                return spec

        # 2. If none of the preferred candidates are on disk, auto-download the best candidate fitting RAM & CPU
        for cand_id in candidates:
            spec = MODEL_CATALOG.get(cand_id)
            if spec and spec.est_ram_mb <= max(1200, avail_mb + 1400):
                if self._ensure_model_downloaded(spec, progress_callback=progress_callback):
                    return spec

        return self.config.resolve_model_for_reasoning(role, effort, avail_mb)

    def _load_spec_instance(self, spec: ModelSpec) -> Any:
        self._ensure_model_downloaded(spec)
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
        reasoning_effort: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Union[GGUFCPUBackend, ONNXCPUBackend]:
        from .gguf_cpu import Llama

        spec = self.ensure_capability_model(
            role=role,
            reasoning_effort=reasoning_effort,
            model_id=model_id,
            progress_callback=progress_callback,
        )

        if spec.backend == "gguf" and (self.config.preferred_backend == "onnx" or Llama is None):
            coder_spec = MODEL_CATALOG["qwen2.5-coder-3b-onnx"]
            if os.path.isdir(coder_spec.abs_path(self.config.models_dir)):
                spec = coder_spec
            else:
                spec = MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"]
        elif self.config.preferred_backend == "gguf" and spec.backend == "onnx" and role in ("primary", "coding") and Llama is not None and (reasoning_effort or "medium") != "low":
            gguf_spec = MODEL_CATALOG["qwen3-4b-gguf"]
            if os.path.exists(gguf_spec.abs_path(self.config.models_dir)):
                spec = gguf_spec

        try:
            return self.arena.acquire(spec, self._load_spec_instance)
        except Exception:
            for fb_id in (spec.fallback_model_id, "qwen2.5-coder-1.5b-onnx", "qwen3.5-0.8b-onnx"):
                if fb_id and fb_id in MODEL_CATALOG and fb_id != spec.model_id:
                    fb = MODEL_CATALOG[fb_id]
                    if fb.backend != "gguf" or Llama is not None:
                        try:
                            return self.arena.acquire(fb, self._load_spec_instance)
                        except Exception:
                            pass
            return self.arena.acquire(MODEL_CATALOG["qwen2.5-coder-1.5b-onnx"], self._load_spec_instance)

    def stream(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        prompt: Optional[str] = None,
        role: str = "primary",
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
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
        llm = self.get_llm(
            role=role,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
            progress_callback=progress_callback,
        )
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
        reasoning_effort: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
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
        llm = self.get_llm(
            role=role,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
            progress_callback=progress_callback,
        )
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
                        "qwen2.5-coder-1.5b-onnx",
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
