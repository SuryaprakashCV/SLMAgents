---
title: "Open Source SLM Agents Community: Privacy-First AI for CPUs"
emoji: 🎯
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
github_repo: t00114218-stack/SLMAgents
---

# SLM Agents

[![Open in Hugging Face](https://huggingface.co/datasets/huggingface/badges/resolve/main/open-in-hf-spaces-sm.svg)](https://huggingface.co/spaces/spcv/slm-agents)

🚀 **Official Website & Interactive Sandbox**: [www.slmagents.ai](https://www.slmagents.ai/)

Welcome to the **SLM Agents** community! This repository is a unified open-source developer portal and Python codebase for running highly-constrained, secure, and privacy-first AI agent workflows locally on standard CPUs using Small Language Models (SLMs). 

If you are looking for a **local AI framework**, **CPU inference library**, or **privacy-first LLM orchestrator**, you are in the right place! No GPU rigs, no subscription costs, and zero network latency. Powered by Pure-CPU `llama.cpp` GGUF (`n_gpu_layers=0`) + Quantized INT4/INT8 ONNX Runtime (`CPUExecutionProvider`).

---

## 10-Plane Pure-CPU Interactive Agent Harness (`./slm`)

`SLMAgents` includes a complete **10-Plane Pure-CPU Agent Harness** (`slm_harness/`) unifying all 26 specialist domains into an interactive terminal TUI/REPL, a headless Unix pipe & JSON-Schema CLI, a 100% air-gapped Web AI Studio, an OpenAI `/v1`-compatible local server, and a native JSON-RPC 2.0 Model Context Protocol (`MCP`) server.

### Quickstart Commands

```bash
# 1. Inspect CPU topology (P-cores, NEON/AVX2/VNNI SIMD), RAM watermarks & 13 quantized models
./slm doctor

# 2. Run empirical Pure-CPU tok/s, TTFT, GBNF JSON Schema & MemoryArena benchmark
./slm bench

# 3. Launch the Interactive Rich Terminal TUI / REPL (with 18 slash commands & @file attachments)
./slm chat

# 4. Headless single-shot & Unix stdin pipe with GBNF / JSON-Schema constrained decoding
./slm ask "Solve 3*x + 9 = 30"
cat broken.json | ./slm ask "Extract clean record" --schema '{"type":"object","properties":{"id":{"type":"integer"}},"required":["id"]}'
./slm ask -f sales.db "Top 5 customers by revenue in 2025"

# 5. Launch 100% Offline Web AI Studio + OpenAI /v1 API Server (http://127.0.0.1:7860/chat.html)
./slm serve --port 7860

# 6. Expose SLMAgents Pure-CPU Harness as a stdio MCP Server for editors & external agents
./slm mcp-serve
```

### 10-Plane Systems Architecture

| Plane | Subsystem (`slm_harness/`) | Capabilities |
| :--- | :--- | :--- |
| **Plane 1** | **Agentic Kernel** (`kernel/`) | Typed `EventBus`, `TurnStateMachine` (`FAST_DIRECT`, `REACT_LOOP` with 2-strike cycle breaker, `PLAN_DAG` with `HandoffEnvelope`, `EVALUATOR_OPTIMIZER`), SQLite WAL `CheckpointStore`, `/fork`, `/rewind`, `/undo` `ShadowSnapshotVFS`, mid-flight `SteerQueue` |
| **Plane 2** | **Constrained Decoding & Tools** (`runtime/constrained.py`, `tools/`) | JSON Schema -> GBNF (`LlamaGrammar`), ONNX Token-Trie `ONNXPrefixEnumMasker`, RFC 8259 deterministic JSON repair, Top-K semantic `ToolRegistry` (15 built-in tools), MCP client & stdio server |
| **Plane 3** | **Context Engineering** (`context/`) | Static-prefix KV-cache `ContextCompiler`, content-addressed `ArtifactStore` (`artifact://<sha12>` descriptors for CSV/XLSX/SQLite/PDF/Image/Audio/Code), 256-token chunked prefill, `/compact` episodic summarizer |
| **Plane 4** | **4-Tier Cognitive Memory** (`memory/`) | T1 `TurnBlackboard`, T2 SQLite WAL `SessionMemoryManager`, T3 `HybridVectorFTS` (SQLite FTS5 BM25 + ONNX embeddings + RRF + `bge-reranker-base` INT8 cross-encoder + `WorkspaceIndexWatcher`), T4 `ProceduralMemory` (`SLM.md` + verified SQL/code exemplars) |
| **Plane 5** | **Pure-CPU Compute & Multimodal** (`runtime/`) | P-core/SIMD thread auto-tuner, LRU `MemoryArena` RAM Governor with OOM tier step-down (`8B -> 4B -> 3B -> 0.8B`), `<40ms` `CancellationToken`, `GGUFCPUBackend`, `ONNXCPUBackend`, `CPUWhisperEngine` (Whisper Small INT8 ONNX STT), `CPUVisionEngine` (`RapidOCR` + `Qwen2-VL-2B` INT4 ONNX) |
| **Plane 6** | **Cascade Router & DAG** (`orchestrator/`) | `<35ms` 4-stage `CascadeRouter` (`explicit/mime -> keyword -> embedding prototype -> 0.8B enum trie`), topological `DAGScheduler`, declarative `.slm/skills/*/SKILL.md` loader, 26 standalone `slm_*` specialists |
| **Plane 7** | **Deterministic Verifiers** (`verifiers/`) | `StreamDegenerationGuard`, sandboxed Python AST/exec verifier, live read-only SQLite verifier with Levenshtein column auto-suggestions, JSON Schema verifier, `[1]` RAG citation grounder, SymPy symbolic math verifier |
| **Plane 8** | **Governance & HITL** (`governance/`) | `L0_PURE`–`L4_SYSTEM_OR_NET` risk policy, `WorkspaceJail`, POSIX `rlimit` subprocess sandbox, secret/PII scrubber, interactive `ApprovalBroker` & `ClarificationBroker` |
| **Plane 9** | **Unified Surfaces** (`surfaces/`, `website/`) | Interactive Rich TUI (`./slm chat`), Unix CLI (`./slm ask`, `./slm run`), 100% air-gapped Web AI Studio (`website/vendor/`), OpenAI `/v1/chat/completions` + `/v1/embeddings` + `/v1/models`, `./slm mcp-serve` |
| **Plane 10** | **Telemetry & Ops** (`telemetry/`) | Hierarchical Waterfall Span Tracer (`/trace`), `./slm doctor`, `./slm bench`, quantized-only `download_models.py` & FP32 duplicate pruner (`./slm models --check-dups`) |

### Pure-CPU RAM Profiles

| Profile | Hard RAM Cap | Primary Reasoning Model | Specialist Engines | Target Hardware |
| :--- | :--- | :--- | :--- | :--- |
| **`lite`** | **2.6 GB** | `qwen3.5-0.8b-onnx` / `qwen2.5-1.5b-onnx` | MiniLM INT4 Embeddings + INT4 Specialists | 8 GB RAM laptops, CI runners, edge VMs |
| **`balanced`** *(default)* | **4.7 GB** | `qwen3-4b-gguf` (`Q4_K_M`, ~26+ tok/s CPU) | Qwen2.5-Coder-3B/Text2SQL/Math INT4 + mxbai INT8 + BGE Reranker INT8 + Whisper Small INT8 | 16 GB RAM laptops & workstations |
| **`power`** | **8.2 GB** | `qwen3-8b-gguf` (`Q4_K_M` hybrid thinking) | Full 13-model catalog with warm LRU arena retention | 24 GB+ RAM multi-core desktops |

---

## Repository Structure

This monorepo is organized into the following main projects:

| Folder | Description | Installation |
| :--- | :--- | :--- |
| [**`slm_orchestrator`**](./slm_orchestrator) | Semantic router powered by local ONNX neural models with dynamic few-shot mapping and autonomous routing. | `pip install slm-orchestrator` |
| [**`slm_rag`**](./slm_rag) | High-efficiency local CPU Retrieval-Augmented Generation library via ONNX Runtime with autonomous tool use. | `pip install slm-rag` |
| [**`slm_summarizer`**](./slm_summarizer) | High-efficiency local CPU text summarization agent via ONNX Runtime with an evaluator-corrector loop. | `pip install slm-summarizer` |
| [**`slm_text_to_sql`**](./slm_text_to_sql) | CPU-optimized Text-to-SQL agent via ONNX Runtime, with built-in QLoRA TPU/GPU fine-tuning. | `pip install slm-text-to-sql` |
| [**`slm_cli_agent`**](./slm_cli_agent) | CPU-optimized command line helper translating language to safe executable commands. | `pip install -e ./slm_cli_agent` |
| [**`slm_code_interpreter`**](./slm_code_interpreter) | Sandboxed Python execution context with auto self-correction traceback recovery loops. | `pip install -e ./slm_code_interpreter` |
| [**`slm_git_repo_manager`**](./slm_git_repo_manager) | Git diff helper translating raw diff streams to conventional commits. | `pip install -e ./slm_git_repo_manager` |
| [**`slm_json_cleaner`**](./slm_json_cleaner) | Pattern mapping utility repairing malformed/broken JSON strings to fit target schemas. | `pip install -e ./slm_json_cleaner` |
| [**`slm_document_parser`**](./slm_document_parser) | Structure-aware layout parser converting PDF/DOCX to markdown and extracting JSON data. | `pip install -e ./slm_document_parser` |
| [**`slm_vision_parser`**](./slm_vision_parser) | Visual layout parser and OCR processor for whiteboard flowcharts and image diagrams. | `pip install -e ./slm_vision_parser` |
| [**`slm_web_agent`**](./slm_web_agent) | Interactive browser controller executing clicks, navigation, and page actions. | `pip install -e ./slm_web_agent` |
| [**`slm_web_scraper`**](./slm_web_scraper) | Fast, offline HTML cleaning utility filtering noise and generating clean text blocks. | `pip install -e ./slm_web_scraper` |
| [**`slm_search_orchestrator`**](./slm_search_orchestrator) | Local search aggregator routing complex questions to web scrapers and synthesis engines. | `pip install -e ./slm_search_orchestrator` |
| [**`slm_db_migration`**](./slm_db_migration) | Legacy schema diffing agent generating migration DDLs verified in SQLite sandboxes. | `pip install -e ./slm_db_migration` |
| [**`slm_email`**](./slm_email) | Secure email inbox analyzer extracting action items and auto-replying with set tone. | `pip install -e ./slm_email` |
| [**`slm_meeting`**](./slm_meeting) | Conversation post-processor formatting action logs and decisions to markdown tables. | `pip install -e ./slm_meeting` |
| [**`slm_voice`**](./slm_voice) | Low-latency audio handler supporting spoken command transcriptions on local CPU. | `pip install -e ./slm_voice` |
| [**`slm_memory`**](./slm_memory) | SQLite-backed entity memory store mapping and retrieving user preferences. | `pip install -e ./slm_memory` |
| [**`slm_task_planner`**](./slm_task_planner) | Autonomous goal decomposition engine mapping dependency DAGs to specialized sub-agents. | `pip install -e ./slm_task_planner` |
| [**`slm_pdf`**](./slm_pdf) | Document conversation RAG interface built on top of the document parser modules. | `pip install -e ./slm_pdf` |
| [**`slm_pkb`**](./slm_pkb) | Markdown indexing agent generating semantic link relationships in personal knowledge bases. | `pip install -e ./slm_pkb` |
| [**`slm_data`**](./slm_data) | Multi-sheet Excel & CSV statistical profiling engine detecting trends and generating direct insights. | `pip install -e ./slm_data` |
| [**`slm_translation`**](./slm_translation) | Multilingual dynamic translation engine supporting Indian and global language pairs. | `pip install -e ./slm_translation` |
| [**`slm_math`**](./slm_math) | Natural language arithmetic parser solving equations step-by-step with symbolic algebra. | `pip install -e ./slm_math` |
| [**`slm_security`**](./slm_security) | Application security auditor scanning code, SQL queries, and endpoints for CWE vulnerabilities and PII leaks. | `pip install -e ./slm_security` |
| [**`slm_embeddings`**](./slm_embeddings) | Local dense vector embeddings server calculating cosine semantic similarity of text. | `pip install -e ./slm_embeddings` |


---

## Getting Started

### SLM Orchestrator
Route user prompts dynamically to specialized agents with robust semantic mapping constraints.
```python
from slm_orchestrator import SLMOrchestrator

orchestrator = SLMOrchestrator()
agents = [
    {"name": "Billing Support", "description": "Handles payment issues"},
    {"name": "General Chat", "description": "casual queries"}
]

selected = orchestrator.route(agents=agents, question="I need invoice help")
print(selected) # Output: Billing Support
```

### SLM RAG
Answer queries locally using context documents with strict guideline compliance.
```python
from slm_rag import SLMRag

rag = SLMRag()
chunks = ["NebulaCorp flagship product is AegisShield."]
answer = rag.answer(
    chunks=chunks,
    question="What is their flagship product?",
    instruction="Answer like a 17th-century pirate."
)
print(answer) # Output: Ahoy matey! AegisShield be their flagship!
```

### SLM Summarizer
Summarize short or large documents locally on standard CPUs using single-pass or Map-Reduce methods.
```python
from slm_summarizer import SLMSummarizer

summarizer = SLMSummarizer()
text = "SpaceX successfully launched its Falcon 9 rocket on Friday..."
summary = summarizer.summarize(
    text=text,
    format="bullet_points",
    instruction="Focus on launch metrics."
)
print(summary)
```

You can also pass inputs as a JSON string or dict mapping prompt and target summary size (number of tokens):
```python
import json

json_input = json.dumps({
    "passage": "SpaceX successfully launched its Falcon 9 rocket...",
    "prompt": "Focus on launch metrics",
    "size": 50,
    "format": "tldr"
})
summary = summarizer.summarize_json(json_input)
print(summary)
```

### SLM Text-to-SQL
Generate SQL queries from natural language database questions on standard CPUs.
```python
from slm_text_to_sql import SLMTextToSQL

agent = SLMTextToSQL()
schema = "CREATE TABLE employees (id INT, name VARCHAR(50), salary INT);"
question = "Get the names of employees earning more than 50000."
query = agent.generate_sql(schema=schema, question=question)
print(query) # Output: SELECT name FROM employees WHERE salary > 50000;
```

### Claude-Style Streaming & Reasoning
All three generation agents (`SLMSummarizer`, `SLMRag`, `SLMTextToSQL`) support Claude-style streaming. When `stream=True` is passed, the model will output its step-by-step thinking inside `<thought>...</thought>` tags first, followed by the final answer. The method returns a Python generator yielding decoded tokens in real-time.

```python
# Real-time streaming with thought-process parsing
stream = rag.answer(
    chunks=["NebulaCorp released AegisShield in 2025."],
    question="What is the release year of AegisShield?",
    instruction="State only the year.",
    stream=True
)

for token in stream:
    # Outputs the reasoning block first in <thought>...</thought> tags,
    # then the final clean answer.
    print(token, end="", flush=True)
```

---

## Execution & Hardware Architecture

All libraries in this monorepo are optimized for local CPU execution via **ONNX Runtime GenAI** and share efficient memory arenas to minimize resource overhead:

*   **Architecture**: Quantized local SLM neural engines.
*   **Privacy**: 100% offline edge execution with zero cloud calls or data telemetry.
*   **Memory Footprint (RAM)**: **~1.5 GB to 2.5 GB** during active inference.
*   **Disk Storage**: Shared model weight cache across libraries to prevent redundant downloads.

---

## Offline Web AI Studio & OpenAI `/v1` Server

Launch the full-stack air-gapped Web AI Studio (with vendored offline Markdown/Highlight.js assets, `<40ms` Stop button, live CPU Profile/Backend switcher, HITL Approval cards, Clarification chips, and Artifact/Citation/Trace Waterfall Inspector Drawer) plus OpenAI-compatible `/v1` endpoints:

```bash
./slm serve --host 127.0.0.1 --port 7860
# Web AI Studio:  http://127.0.0.1:7860/chat.html
# OpenAI Models:  http://127.0.0.1:7860/v1/models
# OpenAI Chat:    http://127.0.0.1:7860/v1/chat/completions
# Embeddings:     http://127.0.0.1:7860/v1/embeddings
```

---

## Contributing

We welcome contributions! Please feel free to open Issues or pull requests to improve any of the 26 live agent frameworks.

Released under the **MIT License**.
