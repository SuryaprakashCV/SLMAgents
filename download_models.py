"""
Quantized Pure-CPU Model Downloader & Optional FP32/FP16 Duplicate Pruner.

Strictly downloads ONLY CPU-quantized artifacts (`Q4_K_M` GGUF and `q4` / `int8` / `quantized`
ONNX weights + tokenizers/configs), avoiding 50+ GB of redundant FP32/FP16 weight files.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List

try:
    from huggingface_hub import hf_hub_download, snapshot_download
except ImportError:
    hf_hub_download = None
    snapshot_download = None


# Quantized-only HuggingFace model catalog (< 5.5 GB total for `balanced` CPU suite)
AGENT_MODELS: List[Dict[str, Any]] = [
    {
        "name": "Qwen3 4B Instruct 2507 Q4_K_M GGUF (Primary Balanced CPU Reasoning & Coding Engine)",
        "kind": "gguf_file",
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "filename": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "dir": "gguf",
        "profiles": ["balanced", "power"],
    },
    {
        "name": "Qwen 3.5 0.8B Quantized ONNX (Fast Router, Lite Profile & Micro-Verifier)",
        "kind": "snapshot",
        "repo": "onnx-community/Qwen3.5-0.8B-ONNX",
        "dir": "qwen3.5-0.8b-onnx",
        "profiles": ["lite", "balanced", "power"],
        "allow_patterns": [
            "*.json",
            "*.jinja",
            "onnx/decoder_model_merged_quantized.*",
            "onnx/embed_tokens_quantized.*",
        ],
    },
    {
        "name": "Qwen 2.5 Coder 3B Q4 ONNX (Pure-ONNX Coding & Structured Generation Engine)",
        "kind": "snapshot",
        "repo": "onnx-community/Qwen2.5-Coder-3B-Instruct",
        "dir": "qwen2.5-coder-3b-onnx",
        "profiles": ["balanced", "power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_q4.onnx*",
        ],
    },
    {
        "name": "Qwen 2.5 Coder Text-to-SQL Q4 ONNX (Specialist SQL AST Engine)",
        "kind": "snapshot",
        "repo": "spcv/qwen2.5_coder_text2sql_onnx",
        "dir": "qwen2.5_coder_text2sql_onnx",
        "profiles": ["lite", "balanced", "power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_q4.onnx*",
        ],
    },
    {
        "name": "Qwen 2.5 Math 1.5B Q4 ONNX (Specialist Math Reasoning Engine)",
        "kind": "snapshot",
        "repo": "onnx-community/Qwen2.5-Math-1.5B-Instruct",
        "dir": "qwen2.5-math-1.5b-onnx",
        "profiles": ["power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_q4.onnx*",
        ],
    },
    {
        "name": "mxbai-embed-large-v1 Quantized ONNX (1024-dim Enterprise RAG Embeddings)",
        "kind": "snapshot",
        "repo": "mixedbread-ai/mxbai-embed-large-v1",
        "dir": "mxbai-embed-large-v1-onnx",
        "profiles": ["balanced", "power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_quantized.onnx*",
        ],
    },
    {
        "name": "all-MiniLM-L6-v2 Q4 ONNX (384-dim Ultra-Fast Embeddings)",
        "kind": "snapshot",
        "repo": "onnx-community/all-MiniLM-L6-v2-ONNX",
        "dir": "all-minilm-l6-v2-onnx",
        "profiles": ["lite", "balanced", "power"],
        "allow_patterns": [
            "*.json",
            "vocab.txt",
            "onnx/model_q4.onnx*",
            "onnx/model_quantized.onnx*",
        ],
    },
    {
        "name": "BGE Reranker Base INT8 ONNX (Cross-Encoder Hybrid RAG Reranker)",
        "kind": "snapshot",
        "repo": "onnx-community/bge-reranker-base-ONNX",
        "dir": "bge-reranker-base-onnx",
        "profiles": ["lite", "balanced", "power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_int8.onnx*",
        ],
    },
    {
        "name": "Whisper Small INT8 ONNX (Offline Pure-CPU Voice Speech-to-Text)",
        "kind": "snapshot",
        "repo": "onnx-community/whisper-small",
        "dir": "whisper-small-onnx",
        "profiles": ["lite", "balanced", "power"],
        "allow_patterns": [
            "*.json",
            "*.txt",
            "onnx/encoder_model_int8.onnx*",
            "onnx/decoder_model_int8.onnx*",
            "onnx/decoder_with_past_model_int8.onnx*",
        ],
    },
    {
        "name": "Qwen2-VL-2B Quantized ONNX (Offline Vision Captioning & VQA)",
        "kind": "snapshot",
        "repo": "onnx-community/Qwen2-VL-2B-Instruct",
        "dir": "qwen2-vl-2b-onnx",
        "profiles": ["balanced", "power"],
        "allow_patterns": [
            "*.json",
            "onnx/vision_encoder_quantized.onnx*",
            "onnx/embed_tokens_int8.onnx*",
            "onnx/decoder_model_merged_q4.onnx*",
        ],
    },
    {
        "name": "Qwen 2.5 Coder 1.5B Q4 ONNX (Fast Technical Specialist)",
        "kind": "snapshot",
        "repo": "onnx-community/Qwen2.5-Coder-1.5B-Instruct",
        "dir": "qwen2.5-coder-1.5b-onnx",
        "profiles": ["power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_q4.onnx*",
        ],
    },
    {
        "name": "Qwen 2.5 1.5B Q4 ONNX (Multilingual & Document Specialist)",
        "kind": "snapshot",
        "repo": "onnx-community/Qwen2.5-1.5B-Instruct",
        "dir": "qwen2.5-1.5b-onnx",
        "profiles": ["power"],
        "allow_patterns": [
            "*.json",
            "onnx/model_q4.onnx*",
        ],
    },
]


def download_profile_models(profile: str = "balanced", models_dir: str | None = None) -> None:
    if snapshot_download is None:
        raise ImportError("Please install huggingface_hub: pip install huggingface_hub")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    target_root = models_dir or os.path.join(base_dir, "models")
    os.makedirs(target_root, exist_ok=True)

    selected = [
        item for item in AGENT_MODELS if profile == "all" or profile in item.get("profiles", ["balanced"])
    ]
    print("==========================================================")
    print(f"  SLMAgents — Quantized Pure-CPU Model Downloader ({profile})")
    print("==========================================================")
    print(f"[*] Verifying/Downloading {len(selected)} quantized CPU models into {target_root}...\n")

    for idx, item in enumerate(selected, 1):
        target_dir = os.path.join(target_root, item["dir"])
        os.makedirs(target_dir, exist_ok=True)
        print(f"[{idx}/{len(selected)}] {item['name']}")
        try:
            if item.get("kind") == "gguf_file" and hf_hub_download is not None:
                dest_file = os.path.join(target_dir, item["filename"])
                if os.path.isfile(dest_file):
                    print(f"    Already present: {dest_file}")
                else:
                    hf_hub_download(
                        repo_id=item["repo"],
                        filename=item["filename"],
                        local_dir=target_dir,
                    )
                    print(f"    Downloaded {item['filename']}")
            else:
                snapshot_download(
                    repo_id=item["repo"],
                    local_dir=target_dir,
                    allow_patterns=item["allow_patterns"],
                )
                print(f"    Verified {item['repo']}")
        except Exception as exc:
            print(f"    Warning: skipped/failed {item['repo']}: {exc}")
        print()


def prune_fp32_duplicates(models_dir: str, apply: bool = False) -> int:
    from slm_harness.telemetry.doctor import find_unquantized_duplicate_onnx

    dups = find_unquantized_duplicate_onnx(models_dir)
    if not dups:
        print("No redundant unquantized FP32/FP16 ONNX files detected.")
        return 0
    total_mb = sum(d["size_mb"] for d in dups)
    print(f"Found {len(dups)} redundant FP32/FP16 files ({total_mb:.1f} MB):")
    removed = 0
    for d in dups:
        print(f"  - {d['file']} ({d['size_mb']} MB)")
        if apply:
            try:
                os.remove(d["file"])
                removed += 1
            except OSError as exc:
                print(f"    Failed to remove: {exc}")
    if not apply:
        print("\nRun with `--prune-fp32` to delete these redundant FP32/FP16 duplicates.")
    else:
        print(f"\nRemoved {removed} files ({total_mb:.1f} MB reclaimed).")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="SLMAgents Quantized Pure-CPU Model Manager")
    parser.add_argument(
        "--profile",
        choices=["lite", "balanced", "power", "all"],
        default="balanced",
        help="CPU profile model bundle to download (default: balanced)",
    )
    parser.add_argument("--check-dups", action="store_true", help="List redundant FP32/FP16 ONNX files")
    parser.add_argument("--prune-fp32", action="store_true", help="Delete redundant FP32/FP16 ONNX files")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(base_dir, "models")

    if args.check_dups or args.prune_fp32:
        prune_fp32_duplicates(models_dir, apply=args.prune_fp32)
        return
    download_profile_models(profile=args.profile, models_dir=models_dir)


if __name__ == "__main__":
    main()
