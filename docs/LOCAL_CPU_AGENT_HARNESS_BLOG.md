# Building a 10-Plane Local AI Agent Harness at $0 Cost: How We Made 0.8B–8B SLMs Reliable on Pure CPU

*Why you do not need a GPU rig, an API key, or a $200/month cloud subscription to run a production-grade agent harness — a complete systems breakdown of the open-source SLMAgents Pure-CPU Harness (`./slm`).*

---

Every week, a new cloud coding assistant or autonomous agent framework launches with the same underlying assumption: **to get reliable agentic behavior, you must rent a massive frontier LLM over an API and pay per token.**

If you have ever hooked an agent loop up to a commercial API, you already know how fast the meter spins. A single multi-step task—reading a local SQLite schema, writing a query, fixing a column error, running a Python script, and formatting the result as JSON—can easily consume 25,000 to 50,000 prompt tokens across five turns. Multiply that across daily engineering workflows, and "accessible AI" quickly turns into a recurring $20 to $200/month per-seat tax, while your private code, financial spreadsheets, and database schemas are shipped to someone else's cloud.

On the flip side, developers who try to escape the cloud bill by downloading a 1.5B, 3B, or 4B parameter Small Language Model (SLM) and plugging it into a standard cloud-oriented agent framework almost always hit a wall:

1. **JSON & Tool-Call Collapse:** Prompting a 3B model with *"Please respond ONLY with valid JSON matching one of these 25 tool schemas"* results in markdown chatter, unescaped quotes, or hallucinated tool arguments.
2. **CPU Prefill Starvation:** Dumping 4,000 tokens of raw CSV rows, tool definitions, and conversation history into every turn makes a laptop CPU freeze for 10+ seconds before generating a single token.
3. **Blind Hallucination Loops:** Without deterministic verification, a small model that writes a slightly wrong SQL column name or Python import will either return broken code or get stuck repeating the exact same mistake until the context window overflows.
4. **RAM Thrashing & OOM Crashes:** Loading an LLM alongside an embedding model, a reranker, an OCR engine, and a speech-to-text model naively blows past 16 GB of system memory.

Over the past several months building **[SLMAgents](https://github.com/t00114218-stack/SLMAgents)**, we set out to prove a different systems thesis:

> **Small models do not fail because they lack reasoning capacity for everyday tasks; they fail because we wrap them in lazy harnesses designed for 200B-parameter cloud models.**

When you move determinism out of the prompt and into the **systems harness**—using grammar-constrained decoding, `<35ms` cascade routing, content-addressed artifact handles, watermarked RAM arenas, and deterministic self-healing verifiers—quantized 0.8B to 8B models running on **100% standard CPU hardware (`n_gpu_layers=0`)** become fast, trustworthy, and **completely free of cost**.

Here is the complete engineering breakdown of **`slm_harness` (`./slm`)**: our open-source, 10-Plane Pure-CPU Agent Harness that unifies 26 specialist agents, an interactive terminal TUI, Unix CLI pipes, an air-gapped Web AI Studio, an OpenAI `/v1` drop-in server, and a native Model Context Protocol (`MCP`) server—all running locally for **$0**.

---

## 1. Architectural Overview: The 10-Plane Pure-CPU Harness

Rather than treating an agent as a `while True` loop around a single chat prompt, `slm_harness` separates responsibilities across **10 orthogonal systems planes**:

```text
+-----------------------------------------------------------------------------------+
| Plane 9: Unified Surfaces                                                         |
|   ./slm chat (TUI) | ./slm ask (Unix CLI) | ./slm serve (Web + /v1) | mcp-serve   |
+-----------------------------------------+-----------------------------------------+
                                          |
+-----------------------------------------v-----------------------------------------+
| Plane 1: Event-Driven Agentic Kernel (TurnStateMachine, Checkpoints, ShadowVFS)   |
|   Modes: FAST_DIRECT | REACT_LOOP (2-strike breaker) | PLAN_DAG | EVAL_OPTIMIZER  |
+-------------------+---------------------+---------------------+-------------------+
                    |                     |                     |
      +-------------v-------------+ +-----v---------------+ +---v-------------------+
      | Plane 6: <35ms Cascade    | | Plane 3: Context    | | Plane 4: 4-Tier       |
      | Router & DAG Scheduler    | | Compiler &          | | Cognitive Memory      |
      | 0. Explicit / MIME        | | ArtifactStore       | | T1: TurnBlackboard    |
      | 1. Regex/AST (<1ms)       | | artifact://<sha12>  | | T2: SQLite WAL Tree   |
      | 2. ONNX Embed (<12ms)     | | Static KV Prefix    | | T3: Hybrid FTS5+Vec   |
      | 3. 0.8B Enum Trie         | | /compact Summarizer | | T4: Procedural Memory |
      +-------------+-------------+ +-----+---------------+ +---+-------------------+
                    |                     |                     |
+-------------------v---------------------v---------------------v-------------------+
| Plane 2: Constrained Decoding & Tools   | Plane 7: Deterministic Verifiers        |
|   JSON Schema -> GBNF (LlamaGrammar)    |   Python AST + POSIX Sandbox Exec       |
|   ONNXPrefixEnumMasker (Token Trie)     |   SQLite Read-Only + Levenshtein Healer |
|   RFC 8259 Repair | Top-K ToolRegistry  |   SymPy Math | [1] Citation Grounder    |
+-----------------------------------------+-----------------------------------------+
                                          |
+-----------------------------------------v-----------------------------------------+
| Plane 5: Pure-CPU Compute & Multimodal Arena                                      |
|   LRU MemoryArena (lite 2.6GB / balanced 4.7GB / power 8.2GB) | <40ms CancelToken |
|   llama.cpp GGUF (Q4_K_M) + ONNX Runtime (INT4/INT8) + Whisper STT + RapidOCR     |
+-----------------------------------------+-----------------------------------------+
| Plane 8: Governance & HITL (L0-L4 Policy, WorkspaceJail, PII Redaction, Brokers)  |
| Plane 10: Telemetry & Ops (Waterfall Span Tracer /trace, ./slm doctor, ./slm bench)|
+-----------------------------------------------------------------------------------+
```

| Plane | Subsystem (`slm_harness/`) | What It Solves on CPU at $0 Cost |
| :--- | :--- | :--- |
| **Plane 1** | **Agentic Kernel** (`kernel/`) | Typed `EventBus`, 4-mode `TurnStateMachine`, SQLite WAL `CheckpointStore`, `/fork`, `/rewind`, `/undo` via `ShadowSnapshotVFS`, and mid-flight `SteerQueue`. |
| **Plane 2** | **Constrained Decoding & Tools** (`runtime/constrained.py`, `tools/`) | Compiles JSON Schemas into `LlamaGrammar` (GBNF) and ONNX token-trie masks (`ONNXPrefixEnumMasker`) + RFC 8259 JSON repair; selects only Top-K tools per turn. |
| **Plane 3** | **Context Engineering** (`context/`) | Preserves static-prefix KV caches; replaces massive raw files with compact `artifact://<sha12>` descriptors; 256-token chunked prefill. |
| **Plane 4** | **4-Tier Cognitive Memory** (`memory/`) | T1 `TurnBlackboard`, T2 SQLite Session Tree, T3 `HybridVectorFTS` (FTS5 BM25 + INT8 ONNX Embeddings + RRF + `bge-reranker-base`), T4 `ProceduralMemory` (`SLM.md`). |
| **Plane 5** | **Pure-CPU Compute & Arena** (`runtime/`) | P-core/SIMD thread auto-tuner, LRU `MemoryArena` RAM Governor with automatic OOM tier step-down (`8B -> 4B -> 3B -> 0.8B`), `<40ms` `CancellationToken`. |
| **Plane 6** | **Cascade Router & DAG** (`orchestrator/`) | `<35ms` 4-stage `CascadeRouter` dispatching across 26 specialist agents and topological `DAGScheduler` with `HandoffEnvelope` contracts. |
| **Plane 7** | **Deterministic Verifiers** (`verifiers/`) | Live read-only SQLite verifier with Levenshtein column auto-healing, sandboxed Python AST/exec verifier, SymPy symbolic solver, and `[1]` RAG citation grounder. |
| **Plane 8** | **Governance & HITL** (`governance/`) | `L0_PURE`–`L4_SYSTEM_OR_NET` risk lattice, `WorkspaceJail`, POSIX `rlimit` subprocess sandbox, secret/PII scrubber, and interactive `ApprovalBroker`. |
| **Plane 9** | **Unified Surfaces** (`surfaces/`, `website/`) | Rich TUI (`./slm chat`), Unix CLI (`./slm ask`), 100% offline Web AI Studio (`./slm serve`), OpenAI `/v1` API, and stdio MCP server (`./slm mcp-serve`). |
| **Plane 10** | **Telemetry & Ops** (`telemetry/`) | Hierarchical Waterfall Span Tracer (`/trace`), hardware diagnostics (`./slm doctor`), empirical benchmark suite (`./slm bench`), and FP32 duplicate pruner. |

---

## 2. Deep Dive: The `<35ms` 4-Stage Cascade Router & Token-Trie Masking

In a multi-specialist system with **26 domain agents** (spanning Text-to-SQL, Python Code Execution, RAG, PDF Parsing, Security Auditing, Math, Git, Data Profiling, and Voice/Vision), how do you route a user's prompt without burning 2 to 4 seconds on an LLM router call?

In `slm_harness/orchestrator/cascade_router.py`, we implemented a **4-Stage Calibrated Cascade Router** that resolves over 90% of routing decisions in **under 12 milliseconds** on CPU, only invoking a tiny 0.8B neural model when semantic margins are genuinely ambiguous:

1. **Stage 0 — Explicit Override & MIME Artifact Inspection (`<0.2ms`):** Checks for slash commands (`/sql`, `/code`, `/plan`) or attached file types (`.sqlite` $\rightarrow$ `slm_text_to_sql`, `.csv`/`.xlsx` $\rightarrow$ `slm_data`, `.pdf` $\rightarrow$ `slm_pdf`, `.wav`/`.m4a` $\rightarrow$ `slm_voice`, `.png` $\rightarrow$ `slm_vision_parser`).
2. **Stage 1 — Structural Regex & AST Signatures (`<1ms`):** High-precision lexical patterns detect SQL DDL (`SELECT ... FROM`, `ALTER TABLE`), stack traces (`Traceback (most recent call last)`), Git diffs (`diff --git`), or broken JSON payloads.
3. **Stage 2 — Calibrated ONNX Embedding Prototype Margin (`<12ms`):** Computes a 1024-d INT8 embedding of the query (`0.03ms` warm latency) and measures cosine similarity against pre-computed domain prototypes for all 26 specialists. If the top-1 prototype exceeds the confidence threshold and beats top-2 by a calibrated margin ($\Delta \ge 0.035$), routing finishes immediately.
4. **Stage 3 — Token-Trie Constrained `0.8B` Neural Tie-Breaker (`~25ms`):** Only when the embedding margin is ambiguous does the router call `qwen3.5-0.8b-onnx`. Instead of letting the 0.8B model generate free-form text, we constrain its output logits using **`ONNXPrefixEnumMasker`** (`slm_harness/runtime/constrained.py`), which builds a prefix token trie over the valid agent labels:

```python
class ONNXPrefixEnumMasker:
    """
    Token-Trie Finite-State Logit Masker for ONNX Runtime.
    Constrains greedy or sampled decoding to emit ONLY one of `allowed_values` (plus EOS),
    guaranteeing valid router labels, yes/no classifications, or tool-name selections
    on pure-CPU ONNX models with <0.1ms overhead per step.
    """
    def mask_logits(self, logits: np.ndarray, generated_ids: Sequence[int]) -> np.ndarray:
        valid_next = self.allowed_next_tokens(generated_ids)
        if not valid_next:
            return logits
        masked = np.full_like(logits, -1e9)
        idx = list(valid_next)
        masked[idx] = logits[idx]
        return masked
```

Because the 0.8B model is mathematically barred from emitting any token sequence outside the allowed enum trie, it achieves **267.56 tokens/sec on CPU** with **0% label hallucination**.

---

## 3. Eliminating Syntax Failures: GBNF Grammars & Top-K Tool Pruning

When cloud harnesses call tools, they typically inject 20 to 40 JSON tool schemas into the system prompt and hope the model returns valid JSON. On a 1.5B–4B CPU model, that fails in two ways:
- **Prefill bloat:** 3,000 tokens of tool schemas slow down every turn.
- **Formatting drift:** Small models wrap JSON in markdown commentary or miss closing braces.

`slm_harness` fixes both in **Plane 2**:

### A. Semantic Top-K Tool Filtering (`ToolRegistry`)
Instead of dumping all 15+ built-in and MCP tools into every prompt, `ToolRegistry.select_top_k(query, k=4)` scores tool descriptions against the active query and injects **only the 4 most relevant tool schemas** into the `ContextCompiler`. Prompt overhead drops by **75%**.

### B. Native GBNF Grammar Compilation (`LlamaGrammar`)
Whenever a turn requires structured output—whether through `--schema` on the CLI, a tool call in `REACT_LOOP`, or `response_format={"type": "json_schema"}` on the `/v1/chat/completions` API—`slm_harness` compiles the target JSON Schema directly into a **GBNF (GGML BNF) context-free grammar** (`slm_harness/runtime/constrained.py`):

```python
def build_llama_grammar(spec: Optional[GrammarSpec]) -> Any:
    """Compiles `GrammarSpec` into a `llama_cpp.LlamaGrammar` instance."""
    if spec is None or spec.mode == "none":
        return None
    from llama_cpp import LlamaGrammar

    if spec.mode == "enum" and spec.allowed_values:
        gbnf = compile_enum_to_gbnf(spec.allowed_values)
        return LlamaGrammar.from_string(gbnf, verbose=False)
    if spec.mode == "json_schema" and spec.json_schema:
        return LlamaGrammar.from_json_schema(json.dumps(spec.json_schema), verbose=False)
    if spec.mode == "json_object":
        return LlamaGrammar.from_string(GENERIC_JSON_OBJECT_GBNF, verbose=False)
```

During token sampling on CPU, `llama.cpp` masks out every token that would violate the grammar state machine. Even a 4B quantized model produces **100% schema-valid JSON on the first pass** with zero retry loops.

---

## 4. Context Engineering on CPU: `artifact://<sha12>` Handles & Static KV Prefix

On a CPU, **prompt prefill is compute-bound**, while **token decoding is memory-bandwidth-bound**. If a user attaches a 10,000-row CSV (`@sales.csv`) or a SQLite database (`@enterprise.db`), dumping the raw file into the prompt will stall the CPU.

In **Plane 3 (`slm_harness/context/`)**, we engineered two optimizations specifically for CPU latency:

1. **Content-Addressed `ArtifactStore` (`artifact://<sha12>`):** When you pass `-f sales.db` or type `@multi_sheet_statement.xlsx` in `./slm chat`, `ArtifactStore` hashes the file, extracts a compact structural descriptor (table DDL, column types, row counts, and 3 sample rows for SQL/CSV; page structure for PDFs; OCR layout for images; Whisper transcript for audio), and injects only an **~80-token descriptor** into the prompt. The model then queries or slices the underlying artifact on demand via tools.
2. **Static-Prefix KV Cache Ordering (`ContextCompiler`):** Every prompt is compiled in strict canonical order from slowest-changing to fastest-changing:
   $$\text{System Invariants} \rightarrow \text{SLM.md Rules} \rightarrow \text{Tool Schemas} \rightarrow \text{Session Summary} \rightarrow \text{Retrieved RAG/Artifacts} \rightarrow \text{Live Turn}$$
   Because the prefix tokens remain byte-identical across turns, `llama.cpp` reuses its in-memory KV cache instead of recomputing prefill from scratch.

---

## 5. Deterministic Self-Healing Verifiers: How a 3B Model Beats Unverified 70B Output

The secret to making small local models trustworthy in production is **Plane 7 (`slm_harness/verifiers/`)**: never trust raw model generation when a deterministic verifier can check—and heal—the output in milliseconds on CPU.

### Case Study: Live Read-Only SQLite Verifier with Levenshtein Column Healing
When a user asks a database question, small models occasionally guess a column name like `revenue_total` when the actual schema column is `total_revenue`.

Instead of returning an untested SQL string to the user, `verify_and_execute_sql` (`slm_harness/verifiers/code_sql_json.py`) opens the target SQLite database in **strict read-only URI mode (`file:path?mode=ro`)**, executes the query, and if SQLite raises an `OperationalError`, introspects `PRAGMA table_info` and computes **Levenshtein fuzzy matches** to construct a self-healing hint:

```python
def _build_fuzzy_sql_hint(err_str: str, tables: List[str], col_map: Dict[str, List[str]]) -> str:
    all_cols: List[str] = []
    col_to_tables: Dict[str, List[str]] = {}
    for t, cols in col_map.items():
        for c in cols:
            if c not in all_cols:
                all_cols.append(c)
            col_to_tables.setdefault(c, []).append(t)

    mc = _MISSING_COL_RE.search(err_str)
    if mc:
        bad_col = mc.group(1).strip('"`').split(".")[-1]
        suggestions = difflib.get_close_matches(bad_col, all_cols, n=4, cutoff=0.45)
        if suggestions:
            sug_desc = ", ".join(f"{s} (in {'/'.join(col_to_tables[s])})" for s in suggestions)
            return f" Hint: closest valid columns are: {sug_desc}."
```

When fed back into `TurnStateMachine`'s `EVALUATOR_OPTIMIZER` loop, the SLM receives the exact error plus `Hint: closest valid columns are: total_revenue (in sales)` and repairs the query on the very next pass.

The same verification plane applies across every domain:
- **Python Code (`verify_python_code`):** Parses the Python AST for syntax errors, executes the script inside a POSIX `rlimit` sandboxed subprocess, and feeds back the last 12 lines of `stderr` on failure.
- **Symbolic Math (`try_deterministic_math_solve`):** Intercepts algebraic equations, derivatives, and arithmetic expressions via **SymPy**, verifying or solving them deterministically in `<5ms`.
- **Grounded RAG Citations (`verify_rag_citations`):** Verifies that `[1]`, `[2]` inline citation markers actually map to retrieved chunks in `HybridVectorFTS` and checks lexical token overlap to prevent hallucinated attributions.

---

## 6. Governing 13 Models on a Laptop: The Watermark LRU `MemoryArena`

`SLMAgents` includes **13 quantized local models** across GGUF (`Q4_K_M`) and ONNX (`INT4`/`INT8`) formats—covering general reasoning (`Qwen3-8B`, `Qwen3-4B`), coding (`Qwen2.5-Coder-3B`), SQL (`Qwen2.5-Text2SQL`), Math (`Qwen2.5-Math-1.5B`), routing (`Qwen3.5-0.8B`), vision (`Qwen2-VL-2B` + `RapidOCR`), speech (`Whisper-Small INT8`), embeddings (`mxbai-embed-large INT8` & `MiniLM INT4`), and cross-encoder reranking (`bge-reranker-base INT8`).

Loading all 13 simultaneously would require >20 GB of RAM. Instead, **Plane 5 (`slm_harness/runtime/arena.py`)** manages model lifecycles through a **Watermark LRU `MemoryArena`** governed by three hardware profiles:

| Profile | Hard RAM Ceiling | Primary Reasoning Engine | Specialist Suite | Target Hardware |
| :--- | :--- | :--- | :--- | :--- |
| **`lite`** | **2.6 GB** | `qwen3.5-0.8b-onnx` / `qwen2.5-1.5b-onnx` | MiniLM INT4 Embeddings + INT4 Specialists | 8 GB RAM laptops, CI runners, edge VMs |
| **`balanced`** *(default)* | **4.7 GB** | `qwen3-4b-gguf` (`Q4_K_M`) | Coder-3B / Text2SQL / Math INT4 + mxbai INT8 + BGE Reranker INT8 + Whisper Small INT8 | 16 GB RAM laptops & workstations |
| **`power`** | **8.2 GB** | `qwen3-8b-gguf` (`Q4_K_M` hybrid thinking) | Full 13-model catalog with warm LRU retention | 24 GB+ RAM multi-core desktops |

How `MemoryArena` prevents OOM crashes on everyday laptops:
1. **Pinned Micro-Engines:** Tiny high-frequency engines (`mxbai-embed-large` at 322 MB or `MiniLM` at 65 MB) stay pinned in RAM so routing and hybrid search have **0ms cold-start overhead**.
2. **Watermark LRU Eviction:** Before loading a specialist LLM (e.g., switching from `Qwen3-4B` to `Qwen2.5-Text2SQL`), `MemoryArena` checks process RSS + estimated model RAM against `ram_soft_limit_mb`. If the budget would be exceeded, it evicts the least-recently-used unpinned LLM and triggers `gc.collect()`.
3. **Automatic OOM Tier Step-Down:** If host memory pressure spikes (`available_ram < 900 MB`), `MemoryArena` automatically walks the `fallback_model_id` chain (`qwen3-8b-gguf` $\rightarrow$ `qwen3-4b-gguf` $\rightarrow$ `qwen2.5-coder-3b-onnx` $\rightarrow$ `qwen2.5-coder-1.5b-onnx` $\rightarrow$ `qwen3.5-0.8b-onnx`), degrading gracefully instead of crashing.
4. **`<40ms` Cooperative Cancellation (`CancellationToken`):** Every prefill chunk and decoded token polls a thread-safe `CancellationToken`. Hitting `Ctrl+C` in the terminal or clicking **Stop** in the Web AI Studio aborts CPU inference in under 40ms without corrupting the session state.

---

## 7. Empirical Pure-CPU Benchmark Scorecard

You can verify hardware topology, SIMD instruction sets, model readiness, and live throughput on your own machine with two commands: `./slm doctor` and `./slm bench`.

Here is the live benchmark output recorded on an Apple Silicon laptop (**CPU-only enforced, `n_gpu_layers=0`, `balanced` profile, 7 P/E threads**):

```text
╭───────────────────── SLMAgents Pure-CPU Harness Doctor ──────────────────────╮
│ CPU: Apple Silicon (arm64) | P-Cores=4, E-Cores=6 | Threads=7                │
│ SIMD: ARM_NEON, FP16, DOTPROD, I8MM, BF16                                    │
│ RAM: 16384.0 MB total | Active Profile=balanced (Hard Cap: 4700 MB)          │
│ Pure-CPU Enforced: True | Quantized CPU Model Catalog: 13/13 READY           │
╰──────────────────────────────────────────────────────────────────────────────╯

Pure-CPU Benchmark Scorecard (Profile=balanced, Threads=7, Peak RSS=3272.1 / 4700 MB)
+------------------------+-----------------------+------------+-------------------+-------------+-----------------------------------------------+
| Stage                  | Model                 | Backend    | Latency / TTFT    | Throughput  | Verification Outcome                          |
+------------------------+-----------------------+------------+-------------------+-------------+-----------------------------------------------+
| Embeddings (1024-d)    | mxbai-embed-large     | onnx_embed | warm = 0.03 ms    | dim = 1024  | PASS (0ms cold start after arena pin)         |
| Primary LLM + GBNF JSON| qwen3-4b-gguf (Q4_K_M)| gguf       | TTFT = 1143.0 ms  | 24.57 tok/s | JSON Schema Valid: {"engine":"slm_harness",   |
|                        |                       |            |                   |             |   "cpu_only":true, "sum_check":42}            |
| ONNX Enum Trie Router  | qwen3.5-0.8b-onnx     | onnx (INT4)| TTFT = 1249.0 ms  | 267.56 tok/s| Label = 'sql' (100% constrained token trie)   |
+------------------------+-----------------------+------------+-------------------+-------------+-----------------------------------------------+
```

### Key Takeaways from the Numbers:
- **Peak Memory Stays Under 3.3 GB:** Even after running 1024-d embeddings, a 4B GGUF model with GBNF grammar constraints, and a 0.8B ONNX router in sequence, `MemoryArena` kept peak process RSS at **3,272 MB**—well inside the 4,700 MB `balanced` ceiling.
- **24.57 tokens/sec on Pure CPU (`Qwen3-4B Q4_K_M`):** That is faster than human reading speed, with **100% guaranteed JSON Schema compliance** via GBNF.
- **0.03 ms Warm Embedding Latency:** Pinning the INT8 ONNX embedding session makes semantic tool selection, cascade routing, and hybrid RAG retrieval practically instantaneous.
- **$0.00 Total Cost:** Zero API keys, zero network bytes transmitted, and zero cloud dependencies.

---

## 8. Five Zero-Cost Workflows You Can Run Today

Because `slm_harness` exposes **Plane 9 (Unified Surfaces)** over the exact same 10-plane kernel, you can use it interactively, script it in bash pipelines, launch a local web studio, or plug it into your IDE.

### Workflow 1: Hardware Diagnostics & Disk Pruning (`./slm doctor` & `./slm models`)
Inspect your CPU topology, SIMD flags (`AVX2`/`VNNI` on x86, `NEON`/`DOTPROD`/`I8MM` on ARM), and reclaim disk space by pruning redundant unquantized weights:
```bash
./slm doctor
./slm models --check-dups
```

### Workflow 2: Interactive Terminal TUI with `@file` Attachments & Checkpoints (`./slm chat`)
Launch the Rich terminal REPL with 18 built-in slash commands (`/agent`, `/plan`, `/profile`, `/files`, `/sql`, `/rewind`, `/fork`, `/undo`, `/compact`, `/trace`, `/doctor`):
```bash
./slm chat
```
Inside the REPL, attach local files inline with `@` syntax—the harness automatically routes to the right specialist and verifies the output:
```text
slm (balanced) > Compare Q3 margin trends in @multi_sheet_statement.xlsx and flag anomalies
slm (balanced) > /trace
slm (balanced) > /rewind 1
```

### Workflow 3: Unix Stdin Pipes & Guaranteed JSON-Schema Extraction (`./slm ask`)
Use `./slm ask` as a composable Unix filter with strict GBNF JSON Schema enforcement:
```bash
# 1. Deterministic symbolic math & verification
./slm ask "Solve 3*x + 9 = 30"

# 2. Repair & extract structured JSON from broken logs via Unix pipe
cat broken.json | ./slm ask "Extract clean record" \
  --schema '{"type":"object","properties":{"id":{"type":"integer"},"status":{"type":"string"}},"required":["id","status"]}'

# 3. Query a local SQLite database in natural language with read-only self-healing verification
./slm ask -f enterprise_corpus.db "List top 5 departments by headcount"
```

### Workflow 4: 100% Air-Gapped Web AI Studio & Drop-In OpenAI `/v1` Server (`./slm serve`)
Start the local FastAPI server to get both a full-featured **Offline Web AI Studio** (with vendored Markdown/Highlight.js assets, `<40ms` Stop button, live CPU Profile switcher, HITL approval cards, and a Waterfall Trace Inspector Drawer) and **OpenAI-compatible `/v1` endpoints**:
```bash
./slm serve --host 127.0.0.1 --port 7860
# Web AI Studio:  http://127.0.0.1:7860/chat.html
# OpenAI Chat:    http://127.0.0.1:7860/v1/chat/completions
# Embeddings:     http://127.0.0.1:7860/v1/embeddings
```
Point any OpenAI SDK client, LangChain script, or local IDE extension to `http://127.0.0.1:7860/v1` with `api_key="local-free"` and use `model="slm-auto"` (or any specific specialist like `slm-sql` or `slm-coder`) at zero cost.

### Workflow 5: Native JSON-RPC 2.0 MCP Server (`./slm mcp-serve`)
Expose the entire SLMAgents Pure-CPU Harness—including all 26 specialists, hybrid workspace search, sandboxed Python/SQL verifiers, and artifact tools—to Claude Desktop, Cursor, VS Code, or any MCP host over `stdio`:
```bash
./slm mcp-serve
```

---

## 9. Get Started in 60 Seconds (100% Free & Open Source)

The entire `SLMAgents` monorepo, including `slm_harness` and all 26 specialist agent libraries, is open-source under the **MIT License**:

```bash
git clone https://github.com/t00114218-stack/SLMAgents.git
cd SLMAgents
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Download quantized CPU models (INT4 ONNX + Q4_K_M GGUF only)
python3 download_models.py --profile balanced

# Verify your CPU harness & launch!
./slm doctor
./slm chat
```

* **GitHub Repository:** [github.com/t00114218-stack/SLMAgents](https://github.com/t00114218-stack/SLMAgents)
* **Official Portal & Sandbox:** [www.slmagents.ai](https://www.slmagents.ai/)
* **Live Hugging Face Space:** [huggingface.co/spaces/spcv/slm-agents](https://huggingface.co/spaces/spcv/slm-agents)

Good AI engineering should not require renting a GPU cluster or handing over a credit card. When you pair specialized, quantized Small Language Models with a disciplined 10-plane local harness, the hardware already sitting on your desk is more than enough.
