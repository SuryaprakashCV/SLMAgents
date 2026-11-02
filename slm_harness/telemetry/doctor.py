"""
Plane 10: Hardware & Model Readiness Doctor (`./slm doctor`) and Pure-CPU Benchmark (`./slm bench`).
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence

from ..config import MODEL_CATALOG, CPUProfile, HarnessConfig, get_config
from ..runtime import GrammarSpec, extract_and_repair_json, get_process_rss_mb, get_runtime
from ..runtime.cpu_topology import detect_cpu_topology


def _dir_or_file_size_mb(path: str) -> float:
    if not os.path.exists(path):
        return 0.0
    if os.path.isfile(path):
        return round(os.path.getsize(path) / (1024 * 1024), 1)
    total = 0
    for root, _dirs, files in os.walk(path):
        for fname in files:
            fp = os.path.join(root, fname)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return round(total / (1024 * 1024), 1)


def _check_sqlite_fts5() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE _t USING fts5(body)")
        conn.close()
        return True
    except Exception:
        return False


def find_unquantized_duplicate_onnx(models_dir: str) -> List[Dict[str, Any]]:
    """
    Identifies redundant FP32/FP16 `.onnx` / `.onnx_data` files in directories that ALREADY
    have working Q4/INT8/quantized ONNX weights so users can reclaim disk space safely.
    """
    reclaimable: List[Dict[str, Any]] = []
    if not os.path.isdir(models_dir):
        return reclaimable

    for entry in sorted(os.listdir(models_dir)):
        onnx_sub = os.path.join(models_dir, entry, "onnx")
        if not os.path.isdir(onnx_sub):
            continue
        files = os.listdir(onnx_sub)
        has_quant = any(
            any(tag in f for tag in ("_q4.onnx", "_int8.onnx", "_quantized.onnx"))
            for f in files
            if f.endswith(".onnx")
        )
        if not has_quant:
            continue
        for fname in sorted(files):
            is_unquant = fname in (
                "model.onnx",
                "model.onnx_data",
                "model_fp16.onnx",
                "model_fp16.onnx_data",
                "decoder_model_merged.onnx",
                "decoder_model_merged.onnx_data",
                "decoder_model_merged_fp16.onnx",
                "decoder_model_merged_fp16.onnx_data",
                "decoder_model.onnx",
                "decoder_model_fp16.onnx",
                "decoder_with_past_model.onnx",
                "decoder_with_past_model_fp16.onnx",
                "encoder_model.onnx",
                "encoder_model_fp16.onnx",
                "embed_tokens.onnx",
                "embed_tokens_fp16.onnx",
                "vision_encoder.onnx",
                "vision_encoder_fp16.onnx",
            )
            if is_unquant:
                fp = os.path.join(onnx_sub, fname)
                try:
                    sz_mb = round(os.path.getsize(fp) / (1024 * 1024), 1)
                except OSError:
                    sz_mb = 0.0
                if sz_mb >= 5.0:
                    reclaimable.append({"model_dir": entry, "file": fp, "size_mb": sz_mb})
    return reclaimable


def run_doctor(config: Optional[HarnessConfig] = None) -> Dict[str, Any]:
    cfg = config or get_config()
    topo = detect_cpu_topology(cfg.n_threads)

    deps: Dict[str, Dict[str, Any]] = {}
    for mod_name, label in [
        ("onnxruntime", "ONNX Runtime (CPUExecutionProvider)"),
        ("llama_cpp", "llama-cpp-python (Pure CPU GGUF + GBNF)"),
        ("tokenizers", "Rust Fast Tokenizers"),
        ("rapidocr_onnxruntime", "RapidOCR ONNX CPU"),
        ("pymupdf", "PyMuPDF Document Engine"),
        ("sympy", "SymPy Symbolic Math Verifier"),
        ("pandas", "Pandas Tabular Engine"),
        ("fastapi", "FastAPI Local Studio Server"),
        ("rich", "Rich Interactive TUI"),
    ]:
        try:
            mod = __import__(mod_name)
            ver = getattr(mod, "__version__", "installed")
            extra = ""
            if mod_name == "onnxruntime":
                provs = mod.get_available_providers()
                extra = f"providers={provs}"
            deps[mod_name] = {"ok": True, "version": str(ver), "label": label, "detail": extra}
        except Exception as exc:
            deps[mod_name] = {"ok": False, "version": None, "label": label, "detail": str(exc)}

    deps["sqlite_fts5"] = {
        "ok": _check_sqlite_fts5(),
        "version": sqlite3.sqlite_version,
        "label": "SQLite3 WAL + FTS5 BM25 Index",
        "detail": "fts5 enabled",
    }

    models_report: List[Dict[str, Any]] = []
    ready_count = 0
    for mid, spec in MODEL_CATALOG.items():
        apath = spec.abs_path(cfg.models_dir)
        exists = os.path.exists(apath)
        if exists:
            ready_count += 1
        models_report.append(
            {
                "model_id": mid,
                "role": spec.role,
                "backend": spec.backend,
                "quantization": spec.quantization,
                "est_ram_mb": spec.est_ram_mb,
                "disk_mb": _dir_or_file_size_mb(apath) if exists else 0.0,
                "ready": exists,
                "path": apath,
            }
        )

    if topo.total_ram_mb >= 14500 and any(
        m["model_id"] == "qwen3-8b-gguf" and m["ready"] for m in models_report
    ):
        rec_profile = CPUProfile.BALANCED.value  # balanced is snappiest default; power available
        max_supported_profile = CPUProfile.POWER.value
    elif topo.total_ram_mb >= 7000:
        rec_profile = CPUProfile.BALANCED.value
        max_supported_profile = CPUProfile.BALANCED.value
    else:
        rec_profile = CPUProfile.LITE.value
        max_supported_profile = CPUProfile.LITE.value

    dups = find_unquantized_duplicate_onnx(cfg.models_dir)
    reclaim_mb = round(sum(d["size_mb"] for d in dups), 1)

    core_ok = (
        deps["onnxruntime"]["ok"]
        and deps["tokenizers"]["ok"]
        and deps["sqlite_fts5"]["ok"]
        and ready_count >= 2
    )

    return {
        "healthy": core_ok,
        "active_profile": cfg.profile.value,
        "recommended_profile": rec_profile,
        "max_supported_profile": max_supported_profile,
        "pure_cpu_enforced": cfg.pure_cpu,
        "cpu": topo.to_dict(),
        "dependencies": deps,
        "models_ready": ready_count,
        "models_total": len(MODEL_CATALOG),
        "models": models_report,
        "reclaimable_unquantized_mb": reclaim_mb,
        "reclaimable_files_count": len(dups),
    }


def run_benchmark(
    max_tokens: int = 48,
    include_onnx_specialist: bool = True,
) -> Dict[str, Any]:
    """
    Runs a live pure-CPU throughput, TTFT, constrained-JSON, and RAM watermark benchmark.
    """
    rt = get_runtime()
    results: List[Dict[str, Any]] = []
    rss_initial = get_process_rss_mb()

    # 1. Embeddings benchmark (cold + warm cache)
    try:
        t0 = time.perf_counter()
        vec = rt.embed_query("Compute quarterly revenue growth by region from SQLite sales table")
        cold_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        t1 = time.perf_counter()
        _ = rt.embed_query("Compute quarterly revenue growth by region from SQLite sales table")
        warm_ms = round((time.perf_counter() - t1) * 1000.0, 3)
        results.append(
            {
                "stage": "embeddings",
                "model_id": rt.get_embedder().spec.model_id,
                "backend": "onnx_embed",
                "dim": int(vec.shape[0]),
                "cold_ms": cold_ms,
                "warm_cache_ms": warm_ms,
                "ok": True,
            }
        )
    except Exception as exc:
        results.append({"stage": "embeddings", "ok": False, "error": str(exc)})

    # 2. Primary Reasoning LLM + Constrained JSON Schema benchmark
    schema = {
        "type": "object",
        "properties": {
            "engine": {"type": "string"},
            "cpu_only": {"type": "boolean"},
            "sum_check": {"type": "integer"},
        },
        "required": ["engine", "cpu_only", "sum_check"],
    }
    try:
        gen = rt.generate(
            role="primary",
            messages=[
                {"role": "system", "content": "Return compact JSON only."},
                {
                    "role": "user",
                    "content": 'Return JSON with engine="slm_harness", cpu_only=true, and sum_check=19+23.',
                },
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            grammar=GrammarSpec.from_schema(schema),
        )
        parsed, err = extract_and_repair_json(gen.text, schema=schema)
        results.append(
            {
                "stage": "primary_llm_constrained_json",
                "model_id": gen.model_id,
                "backend": gen.backend,
                "ttft_ms": gen.ttft_ms,
                "tok_per_sec": gen.tok_per_sec,
                "completion_tokens": gen.completion_tokens,
                "schema_valid": err is None,
                "parsed": parsed,
                "ok": err is None,
            }
        )
    except Exception as exc:
        results.append({"stage": "primary_llm_constrained_json", "ok": False, "error": str(exc)})

    # 3. Fast ONNX Enum-Constrained Router benchmark
    if include_onnx_specialist:
        try:
            allowed = ["sql", "code", "rag", "math", "direct"]
            gen_r = rt.generate(
                role="router",
                model_id="qwen3.5-0.8b-onnx",
                messages=[
                    {"role": "system", "content": "Classify query domain into one label."},
                    {"role": "user", "content": "SELECT department, AVG(salary) FROM employees GROUP BY 1"},
                ],
                max_tokens=6,
                temperature=0.0,
                grammar=GrammarSpec.from_enum(allowed),
            )
            label = gen_r.text.strip()
            results.append(
                {
                    "stage": "onnx_enum_router",
                    "model_id": gen_r.model_id,
                    "backend": gen_r.backend,
                    "ttft_ms": gen_r.ttft_ms,
                    "tok_per_sec": gen_r.tok_per_sec,
                    "selected_label": label,
                    "enum_valid": label in allowed,
                    "ok": label in allowed,
                }
            )
        except Exception as exc:
            results.append({"stage": "onnx_enum_router", "ok": False, "error": str(exc)})

    rss_peak = get_process_rss_mb()
    return {
        "profile": rt.config.profile.value,
        "pure_cpu": rt.config.pure_cpu,
        "threads": rt.topology.recommended_inference_threads,
        "rss_initial_mb": rss_initial,
        "rss_peak_mb": rss_peak,
        "ram_hard_limit_mb": rt.config.ram_hard_limit_mb,
        "within_ram_budget": rss_peak <= rt.config.ram_hard_limit_mb,
        "benchmarks": results,
    }
