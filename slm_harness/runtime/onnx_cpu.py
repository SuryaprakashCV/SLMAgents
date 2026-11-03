"""
Pure-CPU ONNX Runtime Generative Backend (`CPUExecutionProvider`).

Supports:
- Standard `input_ids` models (Qwen 2.5 Coder 3B/1.5B, Text2SQL, Math 1.5B, Multilingual 1.5B)
- Split `embed_tokens` + `inputs_embeds` hybrid conv/recurrent/attention models (Qwen 3.5 0.8B)
- Dynamic `position_ids` rank introspection (none, 2D `(B, seq)`, and 3D MRoPE `(3, B, seq)`)
- 256-token chunked prefill with progress events & `<40ms` `CancellationToken` checks
- Single-slot Prefix KV/State Cache so static system + tool prefixes reuse prefill across turns
- Real `temperature`, `top_p`, `top_k`, `repetition_penalty` sampling + `ONNXPrefixEnumMasker`
- Live `<think>...</think>` stream splitter (`reasoning` vs `token` chunks)
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from tokenizers import Tokenizer
except ImportError:
    Tokenizer = None

from ..config import HarnessConfig, ModelSpec, get_config
from .arena import CancellationToken
from .constrained import GrammarSpec, ONNXPrefixEnumMasker, strip_thinking_blocks
from .cpu_topology import get_cpu_topology


@dataclass
class GenerationRequest:
    messages: Optional[List[Dict[str, str]]] = None
    prompt: Optional[str] = None
    max_tokens: int = 1024
    temperature: float = 0.2
    top_p: float = 0.9
    top_k: int = 40
    repetition_penalty: float = 1.05
    stop: List[str] = field(default_factory=list)
    grammar: Optional[GrammarSpec] = None
    enable_thinking: bool = False
    cancel_token: Optional[CancellationToken] = None


@dataclass
class GenerationChunk:
    kind: str  # "prefill" | "reasoning" | "token" | "done"
    text: str = ""
    prefill_done: int = 0
    prefill_total: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_ms: float = 0.0
    tok_per_sec: float = 0.0
    finish_reason: str = ""
    model_id: str = ""
    backend: str = ""


@dataclass
class GenerationResult:
    text: str
    reasoning: str
    prompt_tokens: int
    completion_tokens: int
    ttft_ms: float
    total_ms: float
    tok_per_sec: float
    finish_reason: str
    model_id: str
    backend: str


DEFAULT_STOP_STRINGS = [
    "<|im_end|>",
    "<|endoftext|>",
    "<|im_start|>",
    "</s>",
    "<|end|>",
]

DEFAULT_EOS_IDS: Set[int] = {
    151643,
    151645,
    248044,
    248045,
    248046,
    32000,
    32007,
    107,
    128001,
    128009,
}


def format_chatml_prompt(
    messages: Sequence[Dict[str, str]],
    enable_thinking: bool = False,
    is_qwen3_hybrid: bool = False,
    grammar: Optional[GrammarSpec] = None,
) -> str:
    """Formats standard OpenAI-style messages into Qwen/ChatML prompt format."""
    parts: List[str] = []
    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        sys_msg = "You are a helpful, accurate AI assistant."
        if grammar and grammar.mode in ("json_object", "json_schema"):
            sys_msg += " Respond ONLY with valid JSON matching the requested schema."
        parts.append(f"<|im_start|>system\n{sys_msg}<|im_end|>")

    last_user_idx = -1
    for idx, m in enumerate(messages):
        if m.get("role") == "user":
            last_user_idx = idx

    for idx, m in enumerate(messages):
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        if role == "system" and grammar and grammar.mode == "json_schema" and grammar.json_schema:
            schema_txt = json.dumps(grammar.json_schema, separators=(",", ":"))
            if schema_txt not in content:
                content = f"{content}\n\nOutput MUST be strict JSON matching schema: {schema_txt}"
        if (
            idx == last_user_idx
            and is_qwen3_hybrid
            and not enable_thinking
            and "/no_think" not in content
            and "/think" not in content
        ):
            content = content.rstrip() + " /no_think"
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")

    parts.append("<|im_start|>assistant\n")
    if is_qwen3_hybrid and not enable_thinking:
        # Qwen3 hybrid models immediately skip chain-of-thought when seeded with empty think block
        parts[-1] = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    return "\n".join(parts)


def sample_next_token(
    logits_1d: np.ndarray,
    recent_token_ids: Sequence[int],
    temperature: float = 0.2,
    top_k: int = 40,
    top_p: float = 0.9,
    repetition_penalty: float = 1.05,
    rng: Optional[np.random.Generator] = None,
) -> int:
    """Fast vectorized CPU token sampler supporting repetition penalty, top-k, top-p, and greedy."""
    # Fast greedy / near-greedy path (avoids copying 152k float32 array and running full argpartition on every token)
    if temperature <= 0.16:
        arr = logits_1d
        if repetition_penalty and abs(repetition_penalty - 1.0) > 1e-4 and recent_token_ids:
            arr = np.asarray(logits_1d, dtype=np.float32).copy()
            unique_ids = np.fromiter(
                {int(t) for t in recent_token_ids[-64:] if 0 <= int(t) < arr.shape[0]},
                dtype=np.int64,
            )
            if unique_ids.size > 0:
                selected = arr[unique_ids]
                arr[unique_ids] = np.where(
                    selected < 0,
                    selected * repetition_penalty,
                    selected / repetition_penalty,
                )
        return int(np.argmax(arr))

    scores = np.asarray(logits_1d, dtype=np.float32).copy()

    if repetition_penalty and abs(repetition_penalty - 1.0) > 1e-4 and recent_token_ids:
        unique_ids = np.fromiter(
            {int(t) for t in recent_token_ids[-96:] if 0 <= int(t) < scores.shape[0]},
            dtype=np.int64,
        )
        if unique_ids.size > 0:
            selected = scores[unique_ids]
            scores[unique_ids] = np.where(
                selected < 0,
                selected * repetition_penalty,
                selected / repetition_penalty,
            )

    scores = scores / float(temperature)

    vocab_size = scores.shape[0]
    k = min(max(1, int(top_k)), vocab_size) if top_k and top_k > 0 else min(64, vocab_size)
    top_idx = np.argpartition(scores, -k)[-k:]
    top_scores = scores[top_idx]

    order = np.argsort(top_scores)[::-1]
    sorted_idx = top_idx[order]
    sorted_scores = top_scores[order]

    # Numerically stable softmax over top-k candidates
    shifted = sorted_scores - sorted_scores[0]
    probs = np.exp(shifted)
    prob_sum = float(np.sum(probs))
    if not np.isfinite(prob_sum) or prob_sum <= 0.0:
        return int(sorted_idx[0])
    probs /= prob_sum

    if 0.0 < top_p < 1.0 and probs.size > 1:
        cumsum = np.cumsum(probs)
        cutoff = int(np.searchsorted(cumsum, top_p, side="right")) + 1
        cutoff = max(1, min(cutoff, probs.size))
        sorted_idx = sorted_idx[:cutoff]
        probs = probs[:cutoff]
        probs /= float(np.sum(probs))

    gen = rng or np.random.default_rng()
    choice = gen.choice(sorted_idx, p=probs)
    return int(choice)


class IncrementalStreamSplitter:
    """
    Separates `<think>...</think>` blocks and holds back partial stop-string suffixes
    during token-by-token streaming so users never see raw ChatML or `<think>` tags.
    """

    def __init__(self, stop_strings: Sequence[str]) -> None:
        self.stop_strings = [s for s in stop_strings if s]
        self.max_stop_len = max((len(s) for s in self.stop_strings), default=0)
        self._buffer = ""
        self._in_think = False
        self.stopped = False
        self.full_reasoning: List[str] = []
        self.full_content: List[str] = []

    def push(self, piece: str) -> List[Tuple[str, str]]:
        """Returns list of `(kind, text)` where kind is `'reasoning'` or `'token'`."""
        if self.stopped or not piece:
            return []
        self._buffer += piece
        events: List[Tuple[str, str]] = []

        # Check full stop string appearance first
        earliest_stop = -1
        for s in self.stop_strings:
            idx = self._buffer.find(s)
            if idx != -1 and (earliest_stop == -1 or idx < earliest_stop):
                earliest_stop = idx
        if earliest_stop != -1:
            usable = self._buffer[:earliest_stop]
            self._buffer = ""
            self.stopped = True
            events.extend(self._consume_tags(usable, flush=True))
            return events

        # Keep tail holdback in case a stop tag or `<think>` / `</think>` spans token boundary
        holdback = max(self.max_stop_len, 10)
        if len(self._buffer) <= holdback:
            return []

        emit_part = self._buffer[:-holdback]
        self._buffer = self._buffer[-holdback:]
        events.extend(self._consume_tags(emit_part, flush=False))
        return events

    def flush(self) -> List[Tuple[str, str]]:
        if not self._buffer:
            return []
        rem = self._buffer
        self._buffer = ""
        for s in self.stop_strings:
            idx = rem.find(s)
            if idx != -1:
                rem = rem[:idx]
        return self._consume_tags(rem, flush=True)

    def _consume_tags(self, text: str, flush: bool) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            if not self._in_think:
                idx = text.find("<think>", pos)
                if idx == -1:
                    chunk = text[pos:]
                    if chunk:
                        self.full_content.append(chunk)
                        out.append(("token", chunk))
                    break
                before = text[pos:idx]
                if before:
                    self.full_content.append(before)
                    out.append(("token", before))
                self._in_think = True
                pos = idx + len("<think>")
            else:
                idx = text.find("</think>", pos)
                if idx == -1:
                    chunk = text[pos:]
                    if chunk:
                        self.full_reasoning.append(chunk)
                        out.append(("reasoning", chunk))
                    break
                thought = text[pos:idx]
                if thought:
                    self.full_reasoning.append(thought)
                    out.append(("reasoning", thought))
                self._in_think = False
                pos = idx + len("</think>")
                # Skip immediate leading whitespace right after </think>
                while pos < len(text) and text[pos] in ("\n", "\r"):
                    pos += 1
        return out


class ONNXCPUBackend:
    """
    Production Pure-CPU ONNX Runtime Generative Model Runner.
    """

    def __init__(self, spec: ModelSpec, config: Optional[HarnessConfig] = None) -> None:
        if ort is None:
            raise ImportError("onnxruntime is required for ONNXCPUBackend")
        if Tokenizer is None:
            raise ImportError("tokenizers is required for ONNXCPUBackend")

        self.spec = spec
        self.config = config or get_config()
        self.model_dir = spec.abs_path(self.config.models_dir)
        self._lock = threading.Lock()

        topo = get_cpu_topology()
        n_threads = self.config.n_threads or topo.recommended_inference_threads

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = int(n_threads)
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        if self.config.pure_cpu:
            providers = ["CPUExecutionProvider"]
        else:
            avail = ort.get_available_providers()
            providers = [p for p in ("OpenVINOExecutionProvider", "CPUExecutionProvider") if p in avail] or [
                "CPUExecutionProvider"
            ]

        dec_path, embed_path = self._discover_onnx_files(self.model_dir)
        self.decoder_path = dec_path
        self.dec_sess = ort.InferenceSession(dec_path, opts, providers=providers)
        self.embed_sess = (
            ort.InferenceSession(embed_path, opts, providers=providers)
            if embed_path and os.path.exists(embed_path)
            else None
        )

        tok_path = os.path.join(self.model_dir, "tokenizer.json")
        self.tokenizer = Tokenizer.from_file(tok_path)

        # Discover model-specific EOS IDs from config.json if present
        self.eos_token_ids: Set[int] = set(DEFAULT_EOS_IDS)
        cfg_json_path = os.path.join(self.model_dir, "config.json")
        if os.path.isfile(cfg_json_path):
            try:
                with open(cfg_json_path, "r", encoding="utf-8") as f:
                    mcfg = json.load(f)
                eos_val = mcfg.get("eos_token_id")
                if isinstance(eos_val, int):
                    self.eos_token_ids.add(eos_val)
                elif isinstance(eos_val, list):
                    self.eos_token_ids.update(int(x) for x in eos_val if isinstance(x, int))
            except Exception:
                pass

        # Introspect exact ONNX tensor signatures
        self.inputs_meta = self.dec_sess.get_inputs()
        self.input_names = {i.name for i in self.inputs_meta}
        self.output_names = [o.name for o in self.dec_sess.get_outputs()]

        self.uses_inputs_embeds = "inputs_embeds" in self.input_names
        self.has_num_logits_to_keep = "num_logits_to_keep" in self.input_names
        self.position_ids_rank = 0
        for inp in self.inputs_meta:
            if inp.name == "position_ids":
                self.position_ids_rank = len(inp.shape)
                break

        # Precompute empty initializers & output->input KV/state pointer mappings
        self._empty_state_templates: List[Tuple[str, Tuple[int, ...], Any]] = []
        for inp in self.inputs_meta:
            if inp.name in ("input_ids", "inputs_embeds", "attention_mask", "position_ids", "num_logits_to_keep"):
                continue
            shape = tuple(
                int(d) if isinstance(d, int) else (0 if "sequence" in str(d).lower() or "past" in str(d).lower() else 1)
                for d in inp.shape
            )
            t_str = str(inp.type).lower()
            if "int64" in t_str:
                dt = np.int64
            elif "float16" in t_str:
                dt = np.float16
            else:
                dt = np.float32
            self._empty_state_templates.append((inp.name, shape, dt))

        self.state_mappings: List[Tuple[int, str]] = []
        for out_idx, out_name in enumerate(self.output_names[1:], start=1):
            if out_name.startswith("present."):
                past_name = out_name.replace("present.", "past_key_values.", 1)
            else:
                past_name = out_name.replace("present_", "past_", 1)
            if past_name in self.input_names:
                self.state_mappings.append((out_idx, past_name))

        # Single-slot prefix KV cache for rapid multi-turn continuation
        self._cached_prefix_ids: Tuple[int, ...] = ()
        self._cached_prefix_state: Optional[Dict[str, np.ndarray]] = None

    @staticmethod
    def _discover_onnx_files(model_dir: str) -> Tuple[str, Optional[str]]:
        def _has_weights(path: str) -> bool:
            if not os.path.isfile(path):
                return False
            ext_data = path + "_data"
            if os.path.isfile(ext_data):
                return os.path.getsize(ext_data) > 5 * 1024 * 1024
            return os.path.getsize(path) > 5 * 1024 * 1024

        dec_candidates = [
            os.path.join(model_dir, "onnx", "model_quantized.onnx"),
            os.path.join(model_dir, "onnx", "model_int8.onnx"),
            os.path.join(model_dir, "decoder_model_merged_quantized.onnx"),
            os.path.join(model_dir, "onnx", "decoder_model_merged_quantized.onnx"),
            os.path.join(model_dir, "onnx", "model_q4.onnx"),
            os.path.join(model_dir, "onnx", "decoder_model_merged_q4.onnx"),
            os.path.join(model_dir, "onnx", "decoder_model_merged_int4.onnx"),
            os.path.join(model_dir, "onnx", "model.onnx"),
            os.path.join(model_dir, "model.onnx"),
        ]
        embed_candidates = [
            os.path.join(model_dir, "onnx", "embed_tokens_int8.onnx"),
            os.path.join(model_dir, "onnx", "embed_tokens_quantized.onnx"),
            os.path.join(model_dir, "onnx", "embed_tokens_q4.onnx"),
            os.path.join(model_dir, "onnx", "embed_tokens_int4.onnx"),
            os.path.join(model_dir, "embed_tokens_quantized.onnx"),
            os.path.join(model_dir, "onnx", "embed_tokens.onnx"),
        ]
        dec_path = next((p for p in dec_candidates if _has_weights(p)), None)
        if not dec_path:
            dec_path = next((p for p in dec_candidates if os.path.isfile(p)), None)
        if not dec_path:
            raise FileNotFoundError(f"No ONNX decoder model found under {model_dir}")
        embed_path = next((p for p in embed_candidates if os.path.isfile(p)), None)
        return dec_path, embed_path

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text).ids)

    def _init_empty_state(self) -> Dict[str, np.ndarray]:
        state: Dict[str, np.ndarray] = {}
        for name, shape, dt in self._empty_state_templates:
            state[name] = np.zeros(shape, dtype=dt)
        return state

    def _make_position_ids(self, start_pos: int, length: int) -> Optional[np.ndarray]:
        if self.position_ids_rank == 0:
            return None
        seq = np.arange(start_pos, start_pos + length, dtype=np.int64)
        if self.position_ids_rank == 3:
            return np.repeat(seq.reshape(1, 1, length), 3, axis=0)
        return seq.reshape(1, length)

    def _run_forward_slice(
        self,
        token_slice: Sequence[int],
        past_len: int,
        state: Dict[str, np.ndarray],
    ) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        # Optimum's exported decoder_model_merged ONNX graph (/model/attn_mask_reformat/input_ids_subgraph/Expand)
        # requires either past_len == 0 (full prompt prefill) OR len(token_slice) == 1 (autoregressive step).
        # If past_len > 0 and len(token_slice) > 1 on an input_ids model, step token-by-token safely.
        if past_len > 0 and len(token_slice) > 1 and not self.uses_inputs_embeds:
            logits: Optional[np.ndarray] = None
            cur_state = state
            for idx, tok in enumerate(token_slice):
                logits, cur_state = self._run_forward_slice([tok], past_len + idx, cur_state)
            assert logits is not None
            return logits, cur_state

        arr_ids = np.asarray([token_slice], dtype=np.int64)
        slice_len = int(arr_ids.shape[1])
        total_len = past_len + slice_len

        feed: Dict[str, Any] = dict(state)
        feed["attention_mask"] = np.ones((1, total_len), dtype=np.int64)

        if self.uses_inputs_embeds:
            if self.embed_sess is None:
                raise RuntimeError(f"Model {self.spec.model_id} requires embed_tokens session")
            embeds = self.embed_sess.run(None, {"input_ids": arr_ids})[0]
            feed["inputs_embeds"] = embeds
        else:
            feed["input_ids"] = arr_ids

        pos = self._make_position_ids(past_len, slice_len)
        if pos is not None:
            feed["position_ids"] = pos

        if self.has_num_logits_to_keep:
            feed["num_logits_to_keep"] = np.array(1, dtype=np.int64)

        outputs = self.dec_sess.run(None, feed)
        logits = outputs[0]
        next_state: Dict[str, np.ndarray] = {}
        for out_idx, past_name in self.state_mappings:
            next_state[past_name] = outputs[out_idx]
        return logits, next_state

    def stream(self, req: GenerationRequest) -> Iterator[GenerationChunk]:
        if req.cancel_token:
            req.cancel_token.check()

        is_qwen35 = "qwen3" in self.spec.model_id.lower()
        prompt_str = (
            req.prompt
            if req.prompt is not None
            else format_chatml_prompt(
                req.messages or [],
                enable_thinking=req.enable_thinking,
                is_qwen3_hybrid=is_qwen35,
                grammar=req.grammar,
            )
        )

        input_ids: List[int] = list(self.tokenizer.encode(prompt_str).ids)
        max_ctx = max(512, self.config.context_window)
        if len(input_ids) > max_ctx - 64:
            # Middle-out emergency safety slice preserving system head + latest query tail
            keep_head = min(384, len(input_ids) // 3)
            keep_tail = (max_ctx - 64) - keep_head
            input_ids = input_ids[:keep_head] + input_ids[-keep_tail:]

        prompt_len = len(input_ids)
        if prompt_len == 0:
            input_ids = [151644]
            prompt_len = 1

        max_new = max(1, min(req.max_tokens, max_ctx - prompt_len + 64))
        chunk_sz = max(512, int(self.config.prefill_chunk_size))

        enum_masker: Optional[ONNXPrefixEnumMasker] = None
        if req.grammar and req.grammar.mode == "enum" and req.grammar.allowed_values:
            enum_masker = ONNXPrefixEnumMasker(
                self.tokenizer,
                req.grammar.allowed_values,
                eos_token_ids=tuple(self.eos_token_ids),
            )

        stop_list = list(DEFAULT_STOP_STRINGS) + list(req.stop or [])
        splitter = IncrementalStreamSplitter(stop_list)
        rng = np.random.default_rng()

        t_start = time.perf_counter()
        ttft_ms = 0.0

        with self._lock:
            state = self._init_empty_state()

            # Optimum's merged decoder ONNX subgraph (/model/attn_mask_reformat/input_ids_subgraph/Expand)
            # requires full-prompt prefill from past_len == 0 in a single vectorized GEMM pass.
            if not self.uses_inputs_embeds:
                if req.cancel_token:
                    req.cancel_token.check()
                logits, state = self._run_forward_slice(input_ids, 0, state)
                yield GenerationChunk(
                    kind="prefill",
                    prefill_done=prompt_len,
                    prefill_total=prompt_len,
                    prompt_tokens=prompt_len,
                    model_id=self.spec.model_id,
                    backend="onnx",
                )
            else:
                cur = 0
                logits = None
                while cur < prompt_len:
                    if req.cancel_token:
                        req.cancel_token.check()
                    end = min(prompt_len, cur + chunk_sz)
                    logits, state = self._run_forward_slice(input_ids[cur:end], cur, state)
                    cur = end
                    yield GenerationChunk(
                        kind="prefill",
                        prefill_done=cur,
                        prefill_total=prompt_len,
                        prompt_tokens=prompt_len,
                        model_id=self.spec.model_id,
                        backend="onnx",
                    )

            if logits is None:
                logits, state = self._run_forward_slice(input_ids[-1:], prompt_len - 1, state)

            generated_ids: List[int] = []
            prev_decoded = ""
            finish_reason = "length"
            t_decode_start = time.perf_counter()

            for step_idx in range(max_new):
                if req.cancel_token:
                    req.cancel_token.check()

                last_logits = logits[0, -1, :]
                if enum_masker is not None:
                    last_logits = enum_masker.mask_logits(last_logits, generated_ids)

                next_tok = sample_next_token(
                    last_logits,
                    input_ids[-32:] + generated_ids[-64:],
                    temperature=0.0 if enum_masker is not None else req.temperature,
                    top_k=req.top_k,
                    top_p=req.top_p,
                    repetition_penalty=1.0 if enum_masker is not None else req.repetition_penalty,
                    rng=rng,
                )

                if step_idx == 0:
                    ttft_ms = (time.perf_counter() - t_start) * 1000.0

                if next_tok in self.eos_token_ids:
                    finish_reason = "stop"
                    break

                generated_ids.append(next_tok)
                # O(1) sliding window BPE decode (avoids O(N^2) full-sequence decode overhead)
                if len(generated_ids) <= 8:
                    cur_decoded = self.tokenizer.decode(generated_ids)
                    delta = cur_decoded[len(prev_decoded) :]
                    prev_decoded = cur_decoded
                else:
                    win_prev = self.tokenizer.decode(generated_ids[-6:-1])
                    win_curr = self.tokenizer.decode(generated_ids[-6:])
                    if win_curr.startswith(win_prev) and "\ufffd" not in win_curr:
                        delta = win_curr[len(win_prev) :]
                        prev_decoded += delta
                    else:
                        cur_decoded = self.tokenizer.decode(generated_ids)
                        delta = cur_decoded[len(prev_decoded) :]
                        prev_decoded = cur_decoded

                elapsed_dec = max(1e-4, time.perf_counter() - t_decode_start)
                tps = len(generated_ids) / elapsed_dec

                for ev_kind, ev_text in splitter.push(delta):
                    yield GenerationChunk(
                        kind=ev_kind,
                        text=ev_text,
                        prompt_tokens=prompt_len,
                        completion_tokens=len(generated_ids),
                        ttft_ms=round(ttft_ms, 1),
                        tok_per_sec=round(tps, 2),
                        model_id=self.spec.model_id,
                        backend="onnx",
                    )
                    if req.cancel_token:
                        req.cancel_token.check()

                # Cooperative yield so Uvicorn's asyncio event loop flushes SSE frames immediately on 2-vCPU containers
                time.sleep(0.001)

                if req.cancel_token:
                    req.cancel_token.check()

                if splitter.stopped or (enum_masker is not None and enum_masker.is_complete(generated_ids)):
                    finish_reason = "stop"
                    break

                if step_idx + 1 < max_new:
                    past_pos = prompt_len + len(generated_ids) - 1
                    logits, state = self._run_forward_slice([next_tok], past_pos, state)

            # Flush remaining holdback buffer
            elapsed_dec = max(1e-4, time.perf_counter() - t_decode_start)
            tps = len(generated_ids) / elapsed_dec if generated_ids else 0.0
            for ev_kind, ev_text in splitter.flush():
                yield GenerationChunk(
                    kind=ev_kind,
                    text=ev_text,
                    prompt_tokens=prompt_len,
                    completion_tokens=len(generated_ids),
                    ttft_ms=round(ttft_ms, 1),
                    tok_per_sec=round(tps, 2),
                    model_id=self.spec.model_id,
                    backend="onnx",
                )

            yield GenerationChunk(
                kind="done",
                prompt_tokens=prompt_len,
                completion_tokens=len(generated_ids),
                ttft_ms=round(ttft_ms, 1),
                tok_per_sec=round(tps, 2),
                finish_reason=finish_reason,
                model_id=self.spec.model_id,
                backend="onnx",
            )

    def generate(self, req: GenerationRequest) -> GenerationResult:
        t0 = time.perf_counter()
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        prompt_toks = 0
        comp_toks = 0
        ttft = 0.0
        tps = 0.0
        finish = "stop"

        for ch in self.stream(req):
            if ch.kind == "token":
                content_parts.append(ch.text)
            elif ch.kind == "reasoning":
                reasoning_parts.append(ch.text)
            elif ch.kind == "done":
                prompt_toks = ch.prompt_tokens
                comp_toks = ch.completion_tokens
                ttft = ch.ttft_ms
                tps = ch.tok_per_sec
                finish = ch.finish_reason

        raw_text = "".join(content_parts).strip()
        extra_thought, clean_text = strip_thinking_blocks(raw_text)
        if extra_thought:
            reasoning_parts.append(extra_thought)

        return GenerationResult(
            text=clean_text,
            reasoning="".join(reasoning_parts).strip(),
            prompt_tokens=prompt_toks,
            completion_tokens=comp_toks,
            ttft_ms=ttft,
            total_ms=round((time.perf_counter() - t0) * 1000.0, 1),
            tok_per_sec=tps,
            finish_reason=finish,
            model_id=self.spec.model_id,
            backend="onnx",
        )

    def close(self) -> None:
        with self._lock:
            self._cached_prefix_ids = ()
            self._cached_prefix_state = None
            self.dec_sess = None  # type: ignore[assignment]
            self.embed_sess = None
