"""
Pure-CPU Multimodal & Retrieval Engines (`CPUExecutionProvider`).

Includes:
1. `CPUEmbeddingEngine`: `mxbai-embed-large-v1-onnx` (1024d) & `all-minilm-l6-v2-onnx` (384d)
2. `CPURerankerEngine`: `bge-reranker-base-onnx` INT8 Cross-Encoder (`model_int8.onnx`)
3. `CPUWhisperEngine`: Real `whisper-small-onnx` INT8 Speech-to-Text (`encoder_model_int8.onnx` +
   `decoder_model_int8.onnx` + `decoder_with_past_model_int8.onnx`) with pure-NumPy 16kHz WAV/audio
   decoding & 80-bin log-mel spectrogram extraction (no `scipy` or GPU required)
4. `CPUVisionEngine`: `RapidOCR` layout-sorted OCR + `Qwen2-VL-2B` quantized ONNX VLM
   (`vision_encoder_quantized.onnx` + `embed_tokens_int8.onnx` + `decoder_model_merged_q4.onnx`)
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from tokenizers import Tokenizer
except ImportError:
    Tokenizer = None

try:
    from PIL import Image
except ImportError:
    Image = None

from ..config import HarnessConfig, ModelSpec, get_config
from .cpu_topology import get_cpu_topology


def _make_cpu_session(onnx_path: str, config: HarnessConfig, threads_cap: Optional[int] = None) -> Any:
    if ort is None:
        raise ImportError("onnxruntime is required")
    topo = get_cpu_topology()
    n_threads = int(config.n_threads or topo.recommended_inference_threads)
    if threads_cap:
        n_threads = min(n_threads, threads_cap)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, n_threads)
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    providers = ["CPUExecutionProvider"] if config.pure_cpu else ort.get_available_providers()
    return ort.InferenceSession(onnx_path, opts, providers=providers)


class CPUEmbeddingEngine:
    """Fast Pure-CPU ONNX Dense Embedding Engine with 512-entry LRU Query Cache."""

    def __init__(self, spec: ModelSpec, config: Optional[HarnessConfig] = None) -> None:
        if Tokenizer is None:
            raise ImportError("tokenizers is required for CPUEmbeddingEngine")
        self.spec = spec
        self.config = config or get_config()
        self.model_dir = spec.abs_path(self.config.models_dir)
        self._lock = threading.Lock()

        candidates = [
            os.path.join(self.model_dir, "onnx", "model_quantized.onnx"),
            os.path.join(self.model_dir, "onnx", "model_int8.onnx"),
            os.path.join(self.model_dir, "onnx", "model_q4.onnx"),
            os.path.join(self.model_dir, "onnx", "model.onnx"),
            os.path.join(self.model_dir, "model.onnx"),
        ]
        onnx_path = next((p for p in candidates if os.path.isfile(p)), None)
        if not onnx_path:
            raise FileNotFoundError(f"No ONNX embedding model found in {self.model_dir}")

        self.session = _make_cpu_session(onnx_path, self.config)
        self.input_names = {i.name for i in self.session.get_inputs()}
        out_shape = self.session.get_outputs()[0].shape
        self.dimension = int(out_shape[-1]) if isinstance(out_shape[-1], int) else 1024

        self.tokenizer = Tokenizer.from_file(os.path.join(self.model_dir, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=min(512, spec.context_length))

        self._cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._cache_max = 512

    def embed_query(self, text: str) -> np.ndarray:
        key = text.strip()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]

        # mxbai retrieval instruction prefix boosts recall on asymmetric queries
        formatted = (
            f"Represent this sentence for searching relevant passages: {key}"
            if "mxbai" in self.spec.model_id and not key.startswith("Represent this sentence")
            else key
        )
        vec = self.embed_batch([formatted])[0]
        with self._lock:
            self._cache[key] = vec
            if len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)
        return vec

    def embed_batch(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)

        all_vecs: List[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            chunk = [t if t.strip() else " " for t in texts[start : start + batch_size]]
            encs = self.tokenizer.encode_batch(chunk)
            max_len = max(max(1, len(e.ids)) for e in encs)
            bsz = len(encs)

            input_ids = np.zeros((bsz, max_len), dtype=np.int64)
            attn_mask = np.zeros((bsz, max_len), dtype=np.int64)
            for row, enc in enumerate(encs):
                L = len(enc.ids)
                if L > 0:
                    input_ids[row, :L] = enc.ids
                    attn_mask[row, :L] = 1

            feed: Dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attn_mask,
            }
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids)

            with self._lock:
                hidden = self.session.run(None, feed)[0]

            if hidden.ndim == 3:
                mask_f = attn_mask[:, :, None].astype(np.float32)
                summed = np.sum(hidden * mask_f, axis=1)
                counts = np.clip(np.sum(mask_f, axis=1), 1e-6, None)
                pooled = summed / counts
            else:
                pooled = hidden.astype(np.float32)

            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            norms = np.where(norms < 1e-8, 1.0, norms)
            all_vecs.append((pooled / norms).astype(np.float32))

        return np.vstack(all_vecs)

    def close(self) -> None:
        with self._lock:
            self._cache.clear()
            self.session = None  # type: ignore[assignment]


class CPURerankerEngine:
    """Pure-CPU BGE INT8 Cross-Encoder Reranker (`bge-reranker-base-onnx`)."""

    def __init__(self, spec: ModelSpec, config: Optional[HarnessConfig] = None) -> None:
        if Tokenizer is None:
            raise ImportError("tokenizers is required for CPURerankerEngine")
        self.spec = spec
        self.config = config or get_config()
        self.model_dir = spec.abs_path(self.config.models_dir)
        self._lock = threading.Lock()

        candidates = [
            os.path.join(self.model_dir, "onnx", "model_int8.onnx"),
            os.path.join(self.model_dir, "onnx", "model_quantized.onnx"),
            os.path.join(self.model_dir, "onnx", "model_q4.onnx"),
            os.path.join(self.model_dir, "onnx", "model.onnx"),
        ]
        onnx_path = next((p for p in candidates if os.path.isfile(p)), None)
        if not onnx_path:
            raise FileNotFoundError(f"No ONNX reranker model found in {self.model_dir}")

        self.session = _make_cpu_session(onnx_path, self.config)
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(os.path.join(self.model_dir, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=min(512, spec.context_length))

    def score_pairs(self, query: str, passages: Sequence[str], batch_size: int = 8) -> List[float]:
        if not passages:
            return []
        scores: List[float] = []
        pairs = [(query, p[:1800]) for p in passages]

        for start in range(0, len(pairs), batch_size):
            sub = pairs[start : start + batch_size]
            encs = self.tokenizer.encode_batch(sub)
            max_len = max(max(1, len(e.ids)) for e in encs)
            bsz = len(encs)

            input_ids = np.zeros((bsz, max_len), dtype=np.int64)
            attn_mask = np.zeros((bsz, max_len), dtype=np.int64)
            for row, enc in enumerate(encs):
                L = len(enc.ids)
                input_ids[row, :L] = enc.ids
                attn_mask[row, :L] = 1

            feed: Dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attn_mask,
            }
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids)

            with self._lock:
                logits = self.session.run(None, feed)[0]

            raw = np.asarray(logits, dtype=np.float32).reshape(-1)
            probs = 1.0 / (1.0 + np.exp(-np.clip(raw, -20.0, 20.0)))
            scores.extend(float(x) for x in probs)
        return scores

    def close(self) -> None:
        with self._lock:
            self.session = None  # type: ignore[assignment]


def _hz_to_mel_slaney(freq: np.ndarray) -> np.ndarray:
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = math.log(6.4) / 27.0
    mels = freq / f_sp
    above = freq >= min_log_hz
    mels[above] = min_log_mel + np.log(freq[above] / min_log_hz) / logstep
    return mels


def _mel_to_hz_slaney(mels: np.ndarray) -> np.ndarray:
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = math.log(6.4) / 27.0
    freqs = f_sp * mels
    above = mels >= min_log_mel
    freqs[above] = min_log_hz * np.exp(logstep * (mels[above] - min_log_mel))
    return freqs


def _build_whisper_mel_filters(sr: int = 16000, n_fft: int = 400, n_mels: int = 80) -> np.ndarray:
    """Builds exact Slaney-normalized 80-bin Whisper Mel filterbank `(80, 201)` in pure NumPy."""
    n_freqs = n_fft // 2 + 1
    fft_freqs = np.linspace(0.0, sr / 2.0, n_freqs, dtype=np.float64)
    mel_min = float(_hz_to_mel_slaney(np.array([0.0]))[0])
    mel_max = float(_hz_to_mel_slaney(np.array([sr / 2.0]))[0])
    mel_pts = np.linspace(mel_min, mel_max, n_mels + 2, dtype=np.float64)
    hz_pts = _mel_to_hz_slaney(mel_pts)
    fdiff = np.diff(hz_pts)
    ramps = np.subtract.outer(hz_pts, fft_freqs)

    weights = np.zeros((n_mels, n_freqs), dtype=np.float64)
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0.0, np.minimum(lower, upper))

    enorm = 2.0 / (hz_pts[2 : n_mels + 2] - hz_pts[:n_mels])
    weights *= enorm[:, np.newaxis]
    return weights.astype(np.float32)


def _compute_whisper_log_mel_numpy(audio_16k: np.ndarray) -> np.ndarray:
    """Pure-NumPy 30-second Whisper log-mel spectrogram `(1, 80, 3000)` without `scipy`."""
    n_samples = 480000  # 30s @ 16kHz
    n_fft = 400
    hop = 160
    wav = np.asarray(audio_16k, dtype=np.float32).reshape(-1)
    if wav.size >= n_samples:
        wav = wav[:n_samples]
    else:
        wav = np.pad(wav, (0, n_samples - wav.size), mode="constant")

    pad = n_fft // 2
    padded = np.pad(wav, (pad, pad), mode="reflect")
    window = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n_fft, dtype=np.float32) / n_fft)).astype(np.float32)

    # Vectorized strided framing: 3001 frames -> drop last frame -> 3000 frames
    n_frames = 1 + (padded.size - n_fft) // hop
    shape = (n_frames, n_fft)
    strides = (padded.strides[0] * hop, padded.strides[0])
    frames = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides)
    windowed = frames[:3000] * window[None, :]
    spec = np.fft.rfft(windowed, n=n_fft, axis=1)
    magnitudes = (np.abs(spec) ** 2).T.astype(np.float32)  # (201, 3000)

    filters = _build_whisper_mel_filters(16000, n_fft, 80)
    mel_spec = filters @ magnitudes
    log_spec = np.log10(np.clip(mel_spec, 1e-10, None))
    log_spec = np.maximum(log_spec, float(np.max(log_spec)) - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec[np.newaxis, :, :].astype(np.float32)


def load_audio_mono_16k(audio_path: str) -> Optional[np.ndarray]:
    """
    Loads `.wav` (8/16/24/32-bit PCM) directly in Python, or converts `.mp3`/`.m4a`/`.ogg`/`.flac`
    via macOS `afconvert` or `ffmpeg` into a 16kHz mono float32 waveform in `[-1.0, 1.0]`.
    Returns `None` if file is not valid audio (e.g. a zero-byte mock test stub).
    """
    if not os.path.isfile(audio_path) or os.path.getsize(audio_path) < 48:
        return None

    def _read_wav(path: str) -> Optional[np.ndarray]:
        try:
            with wave.open(path, "rb") as wf:
                n_ch = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                if n_frames <= 0 or sr <= 0:
                    return None
                raw = wf.readframes(n_frames)
        except Exception:
            return None

        if sampwidth == 2:
            arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sampwidth == 1:
            arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sampwidth == 4:
            arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        elif sampwidth == 3:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            signed = (
                b[:, 0].astype(np.int32)
                | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int8).astype(np.int32) << 16)
            )
            arr = signed.astype(np.float32) / 8388608.0
        else:
            return None

        if n_ch > 1:
            arr = arr.reshape(-1, n_ch).mean(axis=1)

        if sr != 16000 and arr.size > 1:
            duration = arr.size / float(sr)
            target_len = max(1, int(round(duration * 16000)))
            x_old = np.linspace(0.0, 1.0, arr.size, endpoint=False)
            x_new = np.linspace(0.0, 1.0, target_len, endpoint=False)
            arr = np.interp(x_new, x_old, arr).astype(np.float32)
        return arr.astype(np.float32)

    direct = _read_wav(audio_path)
    if direct is not None:
        return direct

    # Convert compressed formats (.mp3, .m4a, .aac, .aiff, .ogg, .flac) via ffmpeg or macOS afconvert
    tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="slm_stt_")
    os.close(tmp_fd)
    try:
        ffmpeg_bin = shutil.which("ffmpeg")
        afconvert_bin = shutil.which("afconvert")
        converted = False
        if ffmpeg_bin:
            res = subprocess.run(
                [ffmpeg_bin, "-y", "-i", audio_path, "-ac", "1", "-ar", "16000", "-f", "wav", tmp_wav],
                capture_output=True,
                timeout=20,
                check=False,
            )
            converted = res.returncode == 0
        if not converted and afconvert_bin:
            res = subprocess.run(
                [afconvert_bin, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", audio_path, tmp_wav],
                capture_output=True,
                timeout=20,
                check=False,
            )
            converted = res.returncode == 0
        if converted:
            return _read_wav(tmp_wav)
        return None
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except Exception:
                pass


class CPUWhisperEngine:
    """
    Real Pure-CPU Whisper Small INT8 ONNX Speech-to-Text Engine.
    Uses `encoder_model_int8.onnx` + `decoder_model_int8.onnx` + `decoder_with_past_model_int8.onnx`.
    """

    def __init__(self, spec: ModelSpec, config: Optional[HarnessConfig] = None) -> None:
        if Tokenizer is None:
            raise ImportError("tokenizers is required for CPUWhisperEngine")
        self.spec = spec
        self.config = config or get_config()
        self.model_dir = spec.abs_path(self.config.models_dir)
        self._lock = threading.Lock()

        onnx_dir = os.path.join(self.model_dir, "onnx")
        enc_path = self._pick_onnx(onnx_dir, "encoder_model")
        dec_init_path = self._pick_onnx(onnx_dir, "decoder_model")
        dec_past_path = self._pick_onnx(onnx_dir, "decoder_with_past_model")

        self.encoder = _make_cpu_session(enc_path, self.config)
        self.decoder_init = _make_cpu_session(dec_init_path, self.config)
        self.decoder_past = _make_cpu_session(dec_past_path, self.config)

        self.tokenizer = Tokenizer.from_file(os.path.join(self.model_dir, "tokenizer.json"))

        gen_cfg_path = os.path.join(self.model_dir, "generation_config.json")
        with open(gen_cfg_path, "r", encoding="utf-8") as f:
            self.gen_cfg = json.load(f)

        self.eos_id = int(self.gen_cfg.get("eos_token_id", 50257))
        self.sot_id = int(self.gen_cfg.get("decoder_start_token_id", 50258))
        self.no_timestamps_id = int(self.gen_cfg.get("no_timestamps_token_id", 50363))
        self.lang_to_id: Dict[str, int] = self.gen_cfg.get("lang_to_id") or {}
        self.task_to_id: Dict[str, int] = self.gen_cfg.get("task_to_id") or {"transcribe": 50359, "translate": 50358}
        self.suppress_tokens = [int(t) for t in (self.gen_cfg.get("suppress_tokens") or [])]
        self.begin_suppress_tokens = [int(t) for t in (self.gen_cfg.get("begin_suppress_tokens") or [])]

        self.dec_init_out_names = [o.name for o in self.decoder_init.get_outputs()]
        self.dec_past_out_names = [o.name for o in self.decoder_past.get_outputs()]
        self.dec_past_in_names = {i.name for i in self.decoder_past.get_inputs()}

        self._hf_extractor = None
        try:
            from transformers import WhisperFeatureExtractor
            self._hf_extractor = WhisperFeatureExtractor.from_pretrained(self.model_dir, local_files_only=True)
        except Exception:
            self._hf_extractor = None

    @staticmethod
    def _pick_onnx(onnx_dir: str, prefix: str) -> str:
        for suffix in ("_int8.onnx", "_quantized.onnx", "_q4.onnx", ".onnx"):
            cand = os.path.join(onnx_dir, f"{prefix}{suffix}")
            if os.path.isfile(cand):
                return cand
        raise FileNotFoundError(f"Missing Whisper ONNX component {prefix} in {onnx_dir}")

    def _resolve_lang_token(self, language: str) -> int:
        lang_map = {
            "english": "en",
            "hindi": "hi",
            "tamil": "ta",
            "telugu": "te",
            "spanish": "es",
            "french": "fr",
            "german": "de",
            "chinese": "zh",
            "japanese": "ja",
            "arabic": "ar",
        }
        code = lang_map.get(language.lower().strip(), language.lower().strip()[:2])
        return int(self.lang_to_id.get(f"<|{code}|>", self.lang_to_id.get("<|en|>", 50259)))

    def _extract_features(self, chunk_16k: np.ndarray) -> np.ndarray:
        if self._hf_extractor is not None:
            try:
                feats = self._hf_extractor(chunk_16k, sampling_rate=16000, return_tensors="np")["input_features"]
                return np.asarray(feats, dtype=np.float32)
            except Exception:
                pass
        return _compute_whisper_log_mel_numpy(chunk_16k)

    def _transcribe_window(self, chunk_16k: np.ndarray, lang_tok: int, task_tok: int, max_tokens: int = 192) -> str:
        # Check for pure silence to avoid Whisper silence hallucinations
        rms = float(np.sqrt(np.mean(np.square(chunk_16k)))) if chunk_16k.size > 0 else 0.0
        if rms < 1e-4:
            return ""

        input_features = self._extract_features(chunk_16k)
        prompt_ids = [self.sot_id, lang_tok, task_tok, self.no_timestamps_id]

        with self._lock:
            enc_hidden = self.encoder.run(None, {"input_features": input_features})[0]
            init_outs = self.decoder_init.run(
                None,
                {
                    "input_ids": np.array([prompt_ids], dtype=np.int64),
                    "encoder_hidden_states": enc_hidden,
                },
            )

            logits = init_outs[0][0, -1, :].copy()
            kv_feed: Dict[str, np.ndarray] = {}
            for name, val in zip(self.dec_init_out_names[1:], init_outs[1:]):
                past_name = name.replace("present.", "past_key_values.", 1)
                if past_name in self.dec_past_in_names:
                    kv_feed[past_name] = val

            generated: List[int] = []
            for step in range(max_tokens):
                if step == 0:
                    for b_tok in self.begin_suppress_tokens:
                        if 0 <= b_tok < logits.shape[0]:
                            logits[b_tok] = -1e9
                for s_tok in self.suppress_tokens:
                    if 0 <= s_tok < logits.shape[0]:
                        logits[s_tok] = -1e9

                # Light repetition penalty against Whisper 3-gram loops
                if len(generated) >= 4:
                    for prev_t in set(generated[-16:]):
                        if 0 <= prev_t < logits.shape[0] and logits[prev_t] > 0:
                            logits[prev_t] /= 1.12

                next_id = int(np.argmax(logits))
                if next_id == self.eos_id or next_id >= 50257:
                    break
                generated.append(next_id)

                if len(generated) >= 12 and len(set(generated[-8:])) == 1:
                    break

                feed = dict(kv_feed)
                feed["input_ids"] = np.array([[next_id]], dtype=np.int64)
                past_outs = self.decoder_past.run(None, feed)
                logits = past_outs[0][0, -1, :].copy()
                for name, val in zip(self.dec_past_out_names[1:], past_outs[1:]):
                    past_name = name.replace("present.", "past_key_values.", 1)
                    if past_name in self.dec_past_in_names:
                        kv_feed[past_name] = val

        if not generated:
            return ""
        return self.tokenizer.decode(generated, skip_special_tokens=True).strip()

    def transcribe(
        self,
        audio_path: str,
        language: str = "english",
        task: str = "transcribe",
    ) -> Optional[str]:
        """
        Transcribes an audio file on pure CPU. Returns `None` ONLY if `audio_path` is not valid audio.
        """
        wav = load_audio_mono_16k(audio_path)
        if wav is None:
            return None

        lang_tok = self._resolve_lang_token(language)
        task_tok = int(self.task_to_id.get(task, 50359))

        window_samples = 480000  # 30s @ 16kHz
        segments: List[str] = []
        for offset in range(0, max(1, wav.size), window_samples):
            piece = wav[offset : offset + window_samples]
            if piece.size < 1600:  # < 0.1s tail
                break
            txt = self._transcribe_window(piece, lang_tok, task_tok)
            if txt:
                segments.append(txt)
        return " ".join(segments).strip()

    def close(self) -> None:
        with self._lock:
            self.encoder = None  # type: ignore[assignment]
            self.decoder_init = None  # type: ignore[assignment]
            self.decoder_past = None  # type: ignore[assignment]


class CPUVisionEngine:
    """
    Pure-CPU Vision & Layout OCR Engine.
    Combines `RapidOCR` (fast bounding-box OCR) with `Qwen2-VL-2B` quantized ONNX
    (`vision_encoder_quantized.onnx` + `embed_tokens_int8.onnx` + `decoder_model_merged_q4.onnx`)
    for genuine visual captioning, diagram comprehension, and VQA on CPU.
    """

    IMAGE_PAD_ID = 151655
    VISION_START_ID = 151652
    VISION_END_ID = 151653
    EOS_IDS = {151643, 151645}

    def __init__(self, spec: ModelSpec, config: Optional[HarnessConfig] = None) -> None:
        self.spec = spec
        self.config = config or get_config()
        self.model_dir = spec.abs_path(self.config.models_dir)
        self._lock = threading.Lock()
        self._ocr: Any = None
        self._vl_loaded = False
        self._vision_sess: Any = None
        self._embed_sess: Any = None
        self._decoder_sess: Any = None
        self._tokenizer: Any = None
        self._img_processor: Any = None
        self._kv_mappings: List[Tuple[int, str]] = []
        self._num_kv_inputs: List[Tuple[str, Tuple[int, ...], Any]] = []

    def _ensure_ocr(self) -> Any:
        if self._ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._ocr = RapidOCR()
            except Exception:
                self._ocr = False
        return self._ocr if self._ocr is not False else None

    def extract_ocr(self, image_path: str) -> Dict[str, Any]:
        ocr = self._ensure_ocr()
        if ocr is None:
            return {"text": "", "lines": [], "items": [], "avg_confidence": 0.0}
        res = None
        try:
            if Image is not None:
                im = Image.open(image_path)
                if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    composited = Image.alpha_composite(bg, rgba).convert("RGB")
                    arr_bgr = np.asarray(composited)[:, :, ::-1].copy()
                    res, _ = ocr(arr_bgr)
                else:
                    res, _ = ocr(image_path)
            else:
                res, _ = ocr(image_path)
        except Exception:
            try:
                res, _ = ocr(image_path)
            except Exception:
                res = None
        if not res:
            return {"text": "", "lines": [], "items": [], "avg_confidence": 0.0}

        # Sort lines top-to-bottom, left-to-right by polygon centroid
        parsed_items: List[Tuple[float, float, str, float]] = []
        for item in res:
            if len(item) >= 3:
                box, txt, conf = item[0], str(item[1]).strip(), float(item[2])
                if not txt:
                    continue
                try:
                    ys = [pt[1] for pt in box]
                    xs = [pt[0] for pt in box]
                    y_top = float(min(ys))
                    x_left = float(min(xs))
                except Exception:
                    y_top, x_left = 0.0, 0.0
                # Bucket rows within 14px vertical tolerance
                row_bucket = round(y_top / 14.0) * 14.0
                parsed_items.append((row_bucket, x_left, txt, conf))

        parsed_items.sort(key=lambda x: (x[0], x[1]))
        lines = [it[2] for it in parsed_items]
        structured_items = [
            {"y": round(it[0], 1), "x": round(it[1], 1), "text": it[2], "confidence": round(it[3], 3)}
            for it in parsed_items
        ]
        avg_conf = sum(it[3] for it in parsed_items) / len(parsed_items) if parsed_items else 0.0
        return {
            "text": "\n".join(lines),
            "lines": lines,
            "items": structured_items,
            "avg_confidence": round(avg_conf, 3),
        }

    def _ensure_vl_onnx(self) -> bool:
        if self._vl_loaded:
            return self._decoder_sess is not None
        self._vl_loaded = True
        try:
            onnx_dir = os.path.join(self.model_dir, "onnx")
            vis_path = next(
                (
                    os.path.join(onnx_dir, f)
                    for f in ("vision_encoder_quantized.onnx", "vision_encoder_uint8.onnx", "vision_encoder_q4f16.onnx")
                    if os.path.isfile(os.path.join(onnx_dir, f))
                ),
                None,
            )
            emb_path = next(
                (
                    os.path.join(onnx_dir, f)
                    for f in ("embed_tokens_int8.onnx", "embed_tokens_quantized.onnx", "embed_tokens_fp16.onnx")
                    if os.path.isfile(os.path.join(onnx_dir, f))
                ),
                None,
            )
            dec_path = next(
                (
                    os.path.join(onnx_dir, f)
                    for f in ("decoder_model_merged_q4.onnx", "decoder_model_merged_int8.onnx", "decoder_model_merged_quantized.onnx")
                    if os.path.isfile(os.path.join(onnx_dir, f))
                ),
                None,
            )
            if not (vis_path and emb_path and dec_path and Tokenizer is not None):
                return False

            from transformers import Qwen2VLImageProcessor

            self._img_processor = Qwen2VLImageProcessor.from_pretrained(self.model_dir, local_files_only=True)
            self._tokenizer = Tokenizer.from_file(os.path.join(self.model_dir, "tokenizer.json"))
            self._vision_sess = _make_cpu_session(vis_path, self.config)
            self._embed_sess = _make_cpu_session(emb_path, self.config)
            self._decoder_sess = _make_cpu_session(dec_path, self.config)

            in_names = {i.name for i in self._decoder_sess.get_inputs()}
            out_names = [o.name for o in self._decoder_sess.get_outputs()]
            for i in self._decoder_sess.get_inputs():
                if i.name.startswith("past_key_values."):
                    shape = tuple(int(d) if isinstance(d, int) else (0 if "sequence" in str(d) else 1) for d in i.shape)
                    dt = np.float16 if "float16" in str(i.type) else np.float32
                    self._num_kv_inputs.append((i.name, shape, dt))
            for idx, oname in enumerate(out_names[1:], start=1):
                pname = oname.replace("present.", "past_key_values.", 1)
                if pname in in_names:
                    self._kv_mappings.append((idx, pname))
            return True
        except Exception:
            self._decoder_sess = None
            return False

    def analyze(
        self,
        image_path: str,
        prompt: str = "Describe this image clearly and accurately.",
        ocr_only: bool = False,
        max_tokens: int = 160,
    ) -> Dict[str, Any]:
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        ocr_info = self.extract_ocr(image_path)
        if ocr_only:
            return {
                "answer": ocr_info["text"] or f"[No visible text detected in {os.path.basename(image_path)}]",
                "ocr_text": ocr_info["text"],
                "ocr_info": ocr_info,
                "engine": "rapidocr-cpu",
            }

        # Run Qwen2-VL-2B quantized ONNX for genuine visual perception
        if Image is not None and self._ensure_vl_onnx():
            try:
                img = Image.open(image_path).convert("RGB")
                # Cap image dimension to 392px on CPU so patch grid stays compact & responsive (<2.5s)
                max_side = 392
                if max(img.size) > max_side:
                    ratio = max_side / float(max(img.size))
                    new_sz = (max(28, int(img.size[0] * ratio)), max(28, int(img.size[1] * ratio)))
                    img = img.resize(new_sz)

                with self._lock:
                    proc = self._img_processor(images=img, return_tensors="np")
                    pixel_values = np.asarray(proc["pixel_values"], dtype=np.float32)
                    grid_thw = np.asarray(proc["image_grid_thw"], dtype=np.int64)

                    vis_feats = self._vision_sess.run(
                        None,
                        {"pixel_values": pixel_values, "grid_thw": grid_thw},
                    )[0]
                    num_img_tokens = int(vis_feats.shape[0])

                    user_q = prompt
                    if ocr_info["text"]:
                        user_q = f"{prompt}\n(Detected OCR text in image: {ocr_info['text'][:600]})"

                    pre_ids = self._tokenizer.encode("<|im_start|>system\nYou are a precise visual assistant.<|im_end|>\n<|im_start|>user\n").ids
                    post_ids = self._tokenizer.encode(f"{user_q}<|im_end|>\n<|im_start|>assistant\n").ids

                    full_ids = (
                        list(pre_ids)
                        + [self.VISION_START_ID]
                        + ([self.IMAGE_PAD_ID] * num_img_tokens)
                        + [self.VISION_END_ID]
                        + list(post_ids)
                    )
                    arr_ids = np.array([full_ids], dtype=np.int64)
                    embeds = self._embed_sess.run(None, {"input_ids": arr_ids})[0].copy()

                    pad_start = len(pre_ids) + 1
                    embeds[0, pad_start : pad_start + num_img_tokens, :] = vis_feats

                    seq_len = len(full_ids)
                    feed: Dict[str, Any] = {
                        "inputs_embeds": embeds,
                        "attention_mask": np.ones((1, seq_len), dtype=np.int64),
                        "position_ids": np.repeat(np.arange(seq_len, dtype=np.int64).reshape(1, 1, seq_len), 3, axis=0),
                    }
                    for kname, kshape, kdt in self._num_kv_inputs:
                        feed[kname] = np.zeros(kshape, dtype=kdt)

                    outs = self._decoder_sess.run(None, feed)
                    logits = outs[0][0, -1, :]

                    gen_ids: List[int] = []
                    cur_pos = seq_len
                    for _ in range(max_tokens):
                        tok = int(np.argmax(logits))
                        if tok in self.EOS_IDS:
                            break
                        gen_ids.append(tok)
                        next_emb = self._embed_sess.run(
                            None, {"input_ids": np.array([[tok]], dtype=np.int64)}
                        )[0]
                        next_feed: Dict[str, Any] = {
                            "inputs_embeds": next_emb,
                            "attention_mask": np.ones((1, cur_pos + 1), dtype=np.int64),
                            "position_ids": np.repeat(
                                np.array([[[cur_pos]]], dtype=np.int64), 3, axis=0
                            ),
                        }
                        for out_idx, pname in self._kv_mappings:
                            next_feed[pname] = outs[out_idx]
                        outs = self._decoder_sess.run(None, next_feed)
                        logits = outs[0][0, -1, :]
                        cur_pos += 1

                    vl_answer = self._tokenizer.decode(gen_ids, skip_special_tokens=True).strip()
                    if vl_answer:
                        return {
                            "answer": vl_answer,
                            "ocr_text": ocr_info["text"],
                            "ocr_info": ocr_info,
                            "engine": "qwen2-vl-2b-onnx-cpu+rapidocr",
                        }
            except Exception:
                pass

        # Fallback: OCR text + image metadata
        meta = ""
        if Image is not None:
            try:
                im = Image.open(image_path)
                meta = f" ({im.size[0]}x{im.size[1]} {im.mode})"
            except Exception:
                pass
        fallback_txt = (
            ocr_info["text"]
            if ocr_info["text"]
            else f"Image {os.path.basename(image_path)}{meta} analyzed on CPU (no embedded text regions detected)."
        )
        return {
            "answer": fallback_txt,
            "ocr_text": ocr_info["text"],
            "ocr_info": ocr_info,
            "engine": "rapidocr-cpu",
        }

    def close(self) -> None:
        with self._lock:
            self._vision_sess = None
            self._embed_sess = None
            self._decoder_sess = None
            self._vl_loaded = False
