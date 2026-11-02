"""
Plane 9: Interactive Rich Terminal TUI/REPL (`./slm chat`) & Headless Unix Pipe/Schema CLI (`./slm`).

Subcommands:
  ./slm [chat]     Interactive CPU Agent Harness REPL with live streaming & 18 slash commands
  ./slm ask        Single-shot & Unix stdin-pipe CLI with `--file`, `--agent`, `--schema`, `--json`
  ./slm run        Declarative YAML workflow batch runner
  ./slm serve      Launch 100% Offline Web AI Studio & OpenAI `/v1` Server (default port 7860)
  ./slm doctor     Hardware SIMD/P-core, RAM watermark, dependency & quantized model check
  ./slm bench      Empirical Pure-CPU tok/s, TTFT, GBNF JSON & MemoryArena benchmark
  ./slm models     Inspect, download, or prune redundant FP32/FP16 model weights
  ./slm mcp-serve  Run SLMAgents CPU Harness as a JSON-RPC 2.0 stdio MCP server
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import select
import signal
import stat
import sys
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import readline
except ImportError:
    readline = None

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    Console = None
    Panel = None
    Table = None
    Text = None

from ..config import ApprovalPolicy, get_config, set_active_profile
from ..context.artifacts import get_artifact_store
from ..context.compactor import compact_session_history
from ..governance.brokers import (
    ApprovalRequest,
    ClarificationRequest,
    get_approval_broker,
    get_clarification_broker,
)
from ..kernel import (
    EventType,
    get_checkpoint_store,
    get_kernel,
    get_shadow_vfs,
)
from ..memory import (
    WorkspaceIndexWatcher,
    get_blackboard,
    get_procedural_memory,
    get_session_memory,
)
from ..orchestrator.cascade_router import AGENT_PROTOTYPES, ExecutionMode
from ..runtime import get_cancellation_registry, get_process_rss_mb, get_runtime
from ..telemetry import get_trace_store, run_benchmark, run_doctor
from ..verifiers.code_sql_json import verify_and_execute_sql

SLASH_COMMANDS = [
    "/help",
    "/agent",
    "/profile",
    "/model",
    "/plan",
    "/attach",
    "/sql",
    "/compact",
    "/fork",
    "/branches",
    "/rewind",
    "/undo",
    "/memory",
    "/approve",
    "/trace",
    "/doctor",
    "/bench",
    "/export",
    "/clear",
    "/exit",
]


def _make_console() -> Any:
    return Console() if Console is not None else None


def render_doctor_cli(as_json: bool = False) -> int:
    report = run_doctor()
    if as_json or Console is None:
        print(json.dumps(report, indent=2))
        return 0 if report["healthy"] else 1

    console = Console()
    cpu = report["cpu"]
    header = (
        f"[bold cyan]CPU:[/bold cyan] {cpu['brand']} ({cpu['arch']}) | "
        f"P-Cores={cpu['performance_cores']}, E-Cores={cpu['efficiency_cores']} | "
        f"Threads={cpu['recommended_inference_threads']}\n"
        f"[bold cyan]SIMD:[/bold cyan] {', '.join(cpu['simd_features'])}\n"
        f"[bold cyan]RAM:[/bold cyan] {cpu['available_ram_mb']} MB free / {cpu['total_ram_mb']} MB total | "
        f"Active Profile=[bold green]{report['active_profile']}[/bold green] "
        f"(Recommended: {report['recommended_profile']}, Max: {report['max_supported_profile']})\n"
        f"[bold cyan]Pure-CPU Enforced:[/bold cyan] {report['pure_cpu_enforced']}"
    )
    console.print(Panel(header, title="SLMAgents Pure-CPU Harness Doctor", border_style="cyan"))

    dep_tbl = Table(title="Runtime Engines & Capabilities", show_lines=False)
    dep_tbl.add_column("Component", style="bold")
    dep_tbl.add_column("Status")
    dep_tbl.add_column("Version")
    dep_tbl.add_column("Details", style="dim")
    for _k, info in report["dependencies"].items():
        status = "[green]READY[/green]" if info["ok"] else "[red]MISSING[/red]"
        dep_tbl.add_row(info["label"], status, str(info["version"] or "-"), info["detail"])
    console.print(dep_tbl)

    mod_tbl = Table(title=f"Quantized CPU Model Catalog ({report['models_ready']}/{report['models_total']} Ready)")
    mod_tbl.add_column("Model ID", style="cyan")
    mod_tbl.add_column("Role")
    mod_tbl.add_column("Backend")
    mod_tbl.add_column("Quant")
    mod_tbl.add_column("Est. RAM")
    mod_tbl.add_column("Disk Size")
    mod_tbl.add_column("State")
    for m in report["models"]:
        st = "[green]READY[/green]" if m["ready"] else "[yellow]NOT DOWNLOADED[/yellow]"
        mod_tbl.add_row(
            m["model_id"],
            m["role"],
            m["backend"],
            m["quantization"],
            f"{m['est_ram_mb']} MB",
            f"{m['disk_mb']} MB" if m["ready"] else "-",
            st,
        )
    console.print(mod_tbl)

    if report["reclaimable_unquantized_mb"] > 100:
        console.print(
            f"[yellow]Tip:[/yellow] Detected {report['reclaimable_files_count']} redundant unquantized FP32/FP16 ONNX files "
            f"([bold]{report['reclaimable_unquantized_mb']} MB[/bold]). Run `./slm models --check-dups` or `--prune-fp32` to reclaim disk space."
        )
    return 0 if report["healthy"] else 1


def render_bench_cli(as_json: bool = False) -> int:
    res = run_benchmark()
    if as_json or Console is None:
        print(json.dumps(res, indent=2))
        return 0

    console = Console()
    tbl = Table(
        title=(
            f"Pure-CPU Benchmark Scorecard (Profile={res['profile']}, Threads={res['threads']}, "
            f"Peak RSS={res['rss_peak_mb']} / {res['ram_hard_limit_mb']} MB)"
        )
    )
    tbl.add_column("Stage", style="bold cyan")
    tbl.add_column("Model")
    tbl.add_column("Backend")
    tbl.add_column("Latency / TTFT")
    tbl.add_column("Throughput")
    tbl.add_column("Verification")

    for b in res["benchmarks"]:
        if not b.get("ok"):
            tbl.add_row(b["stage"], "-", "-", "-", "-", f"[red]FAIL: {b.get('error')}[/red]")
            continue
        if b["stage"] == "embeddings":
            tbl.add_row(
                "Embeddings (1024d)",
                b["model_id"],
                b["backend"],
                f"cold={b['cold_ms']}ms / warm={b['warm_cache_ms']}ms",
                f"dim={b['dim']}",
                "[green]PASS[/green]",
            )
        elif b["stage"] == "primary_llm_constrained_json":
            tbl.add_row(
                "Primary LLM + GBNF JSON",
                b["model_id"],
                b["backend"],
                f"TTFT={b['ttft_ms']}ms",
                f"[bold green]{b['tok_per_sec']} tok/s[/bold green]",
                f"[green]JSON Schema Valid[/green] {b['parsed']}",
            )
        elif b["stage"] == "onnx_enum_router":
            tbl.add_row(
                "ONNX Enum Trie Router",
                b["model_id"],
                b["backend"],
                f"TTFT={b['ttft_ms']}ms",
                f"{b['tok_per_sec']} tok/s",
                f"[green]Label={b['selected_label']!r}[/green]",
            )
    console.print(tbl)
    return 0


def _setup_readline() -> None:
    if readline is None:
        return
    cfg = get_config()
    hist_path = os.path.join(cfg.data_dir, "cli_history")
    try:
        if os.path.isfile(hist_path):
            readline.read_history_file(hist_path)
        readline.set_history_length(500)
    except Exception:
        pass

    agent_names = sorted(AGENT_PROTOTYPES.keys())

    def _completer(text: str, state: int) -> Optional[str]:
        buf = readline.get_line_buffer()
        options: List[str] = []
        if buf.startswith("/agent "):
            prefix = text.lower()
            options = [a for a in (["auto", "list"] + agent_names) if a.startswith(prefix)]
        elif buf.startswith("/profile "):
            options = [p for p in ("lite", "balanced", "power") if p.startswith(text.lower())]
        elif buf.startswith("/approve "):
            options = [p for p in ("auto-safe", "ask", "auto", "strict") if p.startswith(text.lower())]
        elif text.startswith("/"):
            options = [c for c in SLASH_COMMANDS if c.startswith(text)]
        elif text.startswith("@"):
            rel = text[1:]
            options = ["@" + p for p in glob.glob(rel + "*")[:30]]
        else:
            options = [c for c in SLASH_COMMANDS if c.startswith(text)]
        return options[state] if state < len(options) else None

    try:
        readline.parse_and_bind("tab: complete")
        readline.set_completer(_completer)
    except Exception:
        pass


def _save_readline() -> None:
    if readline is None:
        return
    try:
        hist_path = os.path.join(get_config().data_dir, "cli_history")
        readline.write_history_file(hist_path)
    except Exception:
        pass


def run_interactive_chat(
    session_id: Optional[str] = None,
    initial_agent: Optional[str] = None,
) -> int:
    console = _make_console()
    rt = get_runtime()
    cfg = get_config()
    kernel = get_kernel()
    ckpt = get_checkpoint_store()

    # Kick off non-blocking lexical workspace FTS5 indexing in background
    WorkspaceIndexWatcher().start_background_sync(cfg.workspace_dir)
    _setup_readline()

    sid = session_id or f"cli_{uuid.uuid4().hex[:8]}"
    pinned_agent: Optional[str] = initial_agent
    pending_attachments: List[str] = []

    # Wire interactive CLI HITL Approval & Clarification Brokers
    def _cli_approval_handler(req: ApprovalRequest) -> Tuple[bool, bool]:
        if console is not None:
            body = f"[bold]{req.summary}[/bold]\nTool: `{req.tool_name}` (Risk L{req.risk_level})\n\n{req.preview[:1800]}"
            console.print(Panel(body, title="Capability Approval Required", border_style="yellow"))
        else:
            print(f"\n[APPROVAL REQUIRED] {req.summary}\n{req.preview[:1200]}")
        ans = input("Allow execution? [y]es / [n]o / [a]lways for session (default=n): ").strip().lower()
        if ans in ("a", "always"):
            return True, True
        return ans in ("y", "yes"), False

    def _cli_clarify_handler(req: ClarificationRequest) -> str:
        print(f"\n[Clarification Needed] {req.question}")
        for idx, opt in enumerate(req.options, start=1):
            print(f"  {idx}. {opt}")
        raw = input("Select number or type custom answer: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(req.options):
            return req.options[int(raw) - 1]
        return raw or (req.options[0] if req.options else "")

    get_approval_broker().set_interactive_handler(_cli_approval_handler)
    get_clarification_broker().set_interactive_handler(_cli_clarify_handler)

    primary_spec = cfg.resolve_model_spec("primary")
    if console is not None:
        banner = (
            f"[bold green]SLMAgents 10-Plane Pure-CPU Interactive Harness[/bold green]\n"
            f"CPU: [cyan]{rt.topology.brand}[/cyan] ({rt.topology.recommended_inference_threads} threads, {', '.join(rt.topology.simd_features[:4])})\n"
            f"Profile: [bold]{cfg.profile.value}[/bold] (RAM cap {cfg.ram_hard_limit_mb} MB) | "
            f"Primary Model: [cyan]{primary_spec.model_id}[/cyan] ({primary_spec.backend.upper()}) | "
            f"Policy: [yellow]{cfg.approval_policy.value}[/yellow]\n"
            f"Session: `{sid}` (branch `main`) | Type [bold]/help[/bold] for commands, `@file` to attach, `Ctrl+C` to cancel stream."
        )
        console.print(Panel(banner, border_style="green"))
    else:
        print(f"SLMAgents Pure-CPU Harness | Profile={cfg.profile.value} | Primary={primary_spec.model_id}")

    active_run_box: List[Optional[str]] = [None]

    def _sigint_handler(_sig: int, _frame: Any) -> None:
        rid = active_run_box[0]
        if rid:
            get_cancellation_registry().cancel(rid, reason="Interrupted via Ctrl+C")
        else:
            raise KeyboardInterrupt

    orig_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        while True:
            sess_info = ckpt.ensure_session(sid)
            branch_lbl = sess_info["active_branch"]
            agent_lbl = pinned_agent or "auto"
            prompt_prefix = f"\n[{cfg.profile.value}|{branch_lbl}|{agent_lbl}] > "
            try:
                user_in = input(prompt_prefix).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSession saved. Goodbye!")
                break

            if not user_in:
                continue
            if user_in.lower() in ("exit", "quit", "/exit", "/quit"):
                print("Session saved. Goodbye!")
                break

            # --- Handle Slash Commands ---
            if user_in.startswith("/"):
                parts = user_in.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "/help":
                    _print_slash_help(console)
                    continue

                if cmd == "/agent":
                    if not arg or arg == "list":
                        names = ", ".join(sorted(AGENT_PROTOTYPES.keys()))
                        print(f"Active agent: {pinned_agent or 'auto'}\nAvailable (26): {names}")
                    elif arg == "auto":
                        pinned_agent = None
                        print("Switched routing back to 4-Stage Automatic Cascade Router.")
                    else:
                        cand = arg if arg in AGENT_PROTOTYPES else f"slm_{arg}"
                        if cand in AGENT_PROTOTYPES:
                            pinned_agent = cand
                            print(f"Pinned specialist agent: {pinned_agent}")
                        else:
                            print(f"Unknown agent '{arg}'. Use `/agent list`.")
                    continue

                if cmd == "/profile":
                    sub_parts = arg.split()
                    if not sub_parts:
                        print(json.dumps(rt.arena.status(), indent=2))
                    else:
                        prof = sub_parts[0]
                        bck = sub_parts[1] if len(sub_parts) > 1 else None
                        info = rt.switch_profile(prof, preferred_backend=bck)
                        print(
                            f"Switched profile -> {info['profile']} (backend={info['preferred_backend']}, "
                            f"evicted={info['evicted_models']}, RSS={get_process_rss_mb()} MB)"
                        )
                    continue

                if cmd == "/model":
                    print(json.dumps(rt.arena.status(), indent=2))
                    continue

                if cmd == "/attach":
                    if not arg or not os.path.isfile(os.path.expanduser(arg)):
                        print(f"File not found: {arg}")
                    else:
                        entry = get_artifact_store().ingest_file(arg)
                        pending_attachments.append(entry.source_path)
                        if entry.kind == "sqlite":
                            get_blackboard().put(sid, "active_sqlite_db", entry.source_path)
                        print(f"Attached {entry.uri} ({entry.kind}, {entry.byte_size} bytes):\n{entry.descriptor[:500]}")
                    continue

                if cmd == "/sql":
                    if not arg:
                        cur_db = get_blackboard().get(sid, "active_sqlite_db")
                        print(f"Active SQLite DB: {cur_db or '(none — use /sql path/to/file.db)'}")
                    elif os.path.isfile(os.path.expanduser(arg)):
                        entry = get_artifact_store().ingest_file(arg)
                        get_blackboard().put(sid, "active_sqlite_db", entry.source_path)
                        print(f"Bound active SQLite DB -> {entry.source_path}\n{entry.descriptor}")
                    else:
                        cur_db = get_blackboard().get(sid, "active_sqlite_db")
                        ver = verify_and_execute_sql(arg, db_path=cur_db, read_only=True)
                        print(json.dumps(ver.output, indent=2) if ver.passed else ver.error_feedback)
                    continue

                if cmd == "/compact":
                    res = compact_session_history(sid)
                    print(json.dumps(res, indent=2))
                    continue

                if cmd == "/fork":
                    res = ckpt.fork_branch(sid, arg or f"fork-{uuid.uuid4().hex[:4]}")
                    print(f"Switched to branch '{res['active_branch']}' (head={res['head_turn_id']})")
                    continue

                if cmd == "/branches":
                    print(json.dumps(ckpt.list_branches(sid), indent=2))
                    continue

                if cmd == "/rewind":
                    n = int(arg) if arg.isdigit() else 1
                    res = ckpt.rewind_turns(sid, turns_to_pop=n, vfs=get_shadow_vfs())
                    print(f"Rewound {res['rewound_nodes']} nodes. Restored files: {res['restored_files']}")
                    continue

                if cmd == "/undo":
                    restored = get_shadow_vfs().undo_run(sid)
                    print(f"Undid last workspace mutations: {restored or '(no file mutations recorded)'}")
                    continue

                if cmd == "/memory":
                    pm = get_procedural_memory()
                    if arg.startswith("add "):
                        pm.remember_fact(arg[4:].strip(), workspace=cfg.workspace_dir)
                        print("Stored fact in ProceduralMemory.")
                    elif arg.startswith("del "):
                        n_del = pm.delete_fact(arg[4:].strip())
                        print(f"Deleted {n_del} matching facts.")
                    else:
                        facts = pm.list_facts(workspace=cfg.workspace_dir)
                        print("Remembered Facts:\n" + ("\n".join(f"  - {f}" for f in facts) or "  (none)"))
                    continue

                if cmd == "/approve":
                    try:
                        cfg.approval_policy = ApprovalPolicy(arg.lower())
                        print(f"Approval policy set to: {cfg.approval_policy.value}")
                    except ValueError:
                        print("Valid policies: auto-safe | ask | auto | strict")
                    continue

                if cmd == "/trace":
                    tr = get_trace_store().get_trace(arg or None)
                    print(tr.format_ascii_waterfall() if tr else "No trace recorded yet.")
                    continue

                if cmd == "/doctor":
                    render_doctor_cli(as_json=False)
                    continue

                if cmd == "/bench":
                    render_bench_cli(as_json=False)
                    continue

                if cmd == "/export":
                    exp_parts = arg.split()
                    fmt = exp_parts[0] if exp_parts and exp_parts[0] in ("md", "markdown", "json") else "markdown"
                    out_file = exp_parts[1] if len(exp_parts) > 1 else os.path.join(cfg.workspace_dir, f"{sid}_export.{'json' if fmt == 'json' else 'md'}")
                    body = get_session_memory().export_session(sid, fmt=fmt)
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(body)
                    print(f"Exported session to {out_file} ({len(body)} chars)")
                    continue

                if cmd == "/clear":
                    ckpt.clear_session(sid)
                    get_blackboard().clear_session(sid)
                    pending_attachments.clear()
                    print(f"Cleared session '{sid}'.")
                    continue

                if cmd == "/plan":
                    if not arg:
                        print("Usage: /plan <multi-step goal>")
                        continue
                    user_in = arg
                    forced_mode: Optional[ExecutionMode] = ExecutionMode.PLAN_DAG
                else:
                    print(f"Unknown command '{cmd}'. Type /help for available commands.")
                    continue
            else:
                forced_mode = None

            # --- Execute Live Turn with Real-Time Token Streaming ---
            rid = f"run_{uuid.uuid4().hex[:10]}"
            active_run_box[0] = rid
            turn_atts = list(pending_attachments)
            pending_attachments.clear()

            printed_header = False
            streamed_any = False

            for ev in kernel.stream_turn(
                user_query=user_in,
                session_id=sid,
                run_id=rid,
                attachments=turn_atts,
                pinned_agent=pinned_agent,
                forced_mode=forced_mode,
            ):
                et = ev.event_type
                p = ev.payload
                if et == EventType.ROUTE_DECIDED:
                    sys.stdout.write(
                        f"\033[90m  [route] {p.get('primary_agent')} • {p.get('mode')} • {p.get('stage_used')} ({p.get('latency_ms')}ms)\033[0m\n"
                    )
                    sys.stdout.flush()
                elif et == EventType.PLAN_CREATED:
                    steps = p.get("steps") or []
                    sys.stdout.write(f"\033[36m  [plan] {len(steps)}-node DAG compiled:\033[0m\n")
                    for st in steps:
                        sys.stdout.write(f"\033[90m    - {st['node_id']} ({st['agent']}): {st['instruction'][:95]}\033[0m\n")
                    sys.stdout.flush()
                elif et == EventType.TOOL_EXEC_STARTED:
                    sys.stdout.write(f"\033[33m  [tool] {p.get('tool')}({json.dumps(p.get('arguments', {}))[:100]})\033[0m\n")
                    sys.stdout.flush()
                elif et == EventType.TOOL_RESULT:
                    mark = "ok" if p.get("ok") else "err"
                    sys.stdout.write(f"\033[90m  [tool:{mark}] {p.get('tool')} ({p.get('duration_ms')}ms)\033[0m\n")
                    sys.stdout.flush()
                elif et == EventType.VERIFIER_PASSED:
                    sys.stdout.write(f"\033[32m  [verified] {p.get('agent')} passed deterministic check\033[0m\n")
                    sys.stdout.flush()
                elif et == EventType.REASONING_DELTA:
                    txt = p.get("text", "")
                    if txt:
                        sys.stdout.write(f"\033[90m{txt}\033[0m")
                        sys.stdout.flush()
                elif et == EventType.TOKEN_DELTA:
                    if not printed_header:
                        sys.stdout.write("\n")
                        printed_header = True
                    txt = p.get("text", "")
                    sys.stdout.write(txt)
                    sys.stdout.flush()
                    streamed_any = True
                elif et == EventType.RUN_CANCELLED:
                    sys.stdout.write(f"\n\033[33m[Cancelled in {p.get('total_ms')}ms]\033[0m\n")
                    sys.stdout.flush()
                elif et == EventType.RUN_ERROR:
                    sys.stdout.write(f"\n\033[31m[Error] {p.get('error')}\033[0m\n")
                    sys.stdout.flush()
                elif et == EventType.RUN_COMPLETED:
                    if streamed_any:
                        sys.stdout.write("\n")
                    tps_str = f"{p.get('tok_per_sec')} tok/s • " if p.get("tok_per_sec") else ""
                    ttft_str = f"TTFT {p.get('ttft_ms')}ms • " if p.get("ttft_ms") else ""
                    mdl_str = f"{p.get('model_id')} • " if p.get("model_id") else ""
                    sys.stdout.write(
                        f"\033[90m  [{mdl_str}{ttft_str}{tps_str}total {p.get('total_ms')}ms • RSS {get_process_rss_mb()} MB]\033[0m\n"
                    )
                    sys.stdout.flush()

            active_run_box[0] = None
    finally:
        signal.signal(signal.SIGINT, orig_sigint)
        _save_readline()

    return 0


def _print_slash_help(console: Any) -> None:
    rows = [
        ("/agent [name|auto|list]", "Pin one of the 26 specialist agents or reset to automatic routing"),
        ("/profile <lite|balanced|power> [gguf|onnx]", "Switch CPU RAM ceiling & backend live"),
        ("/model", "Inspect active loaded models in MemoryArena & RSS watermarks"),
        ("/plan <goal>", "Force multi-agent topological PLAN_DAG execution"),
        ("/attach <path>  (or inline @path)", "Attach PDF, CSV, Excel, SQLite, Image, Audio, or Code file"),
        ("/sql <db_path | SELECT ...>", "Bind a SQLite database or run an immediate read-only SQL query"),
        ("/compact", "Summarize older turns into an episodic checkpoint to free context"),
        ("/fork <branch>  &  /branches", "Branch the conversation tree or switch branches"),
        ("/rewind [n]", "Rewind n turns and atomically revert workspace file mutations"),
        ("/undo", "Revert workspace file edits/writes from the last turn"),
        ("/memory [add <fact> | del <text>]", "View or manage persistent cross-session ProceduralMemory"),
        ("/approve <auto-safe|ask|auto|strict>", "Configure L0-L4 capability approval policy"),
        ("/trace [run_id]", "Render ASCII Waterfall Span Trace of the last turn"),
        ("/doctor  &  /bench", "Run CPU hardware/model diagnostics or live throughput benchmark"),
        ("/export [markdown|json] [path]", "Export full session history with citations"),
        ("/clear  &  /exit", "Clear active session or exit"),
    ]
    if console is not None and Table is not None:
        tbl = Table(title="SLMAgents Interactive Slash Commands")
        tbl.add_column("Command", style="cyan")
        tbl.add_column("Description")
        for c, d in rows:
            tbl.add_row(c, d)
        console.print(tbl)
    else:
        for c, d in rows:
            print(f"  {c:38s} {d}")


def run_headless_ask(
    query_parts: Sequence[str],
    files: Sequence[str] = (),
    agent: Optional[str] = None,
    plan_mode: bool = False,
    schema_arg: Optional[str] = None,
    output_json: bool = False,
    session_id: str = "cli_headless",
    max_tokens: int = 1024,
) -> int:
    user_q = " ".join(query_parts).strip()
    piped_stdin = ""
    if not sys.stdin.isatty():
        try:
            fd = sys.stdin.fileno()
            st_mode = os.fstat(fd).st_mode
            if stat.S_ISREG(st_mode):
                piped_stdin = sys.stdin.read()
            elif stat.S_ISFIFO(st_mode) or stat.S_ISSOCK(st_mode):
                # Avoid hanging forever when invoked from subprocess/CI runners that leave stdin open
                wait_sec = 0.25 if not user_q else 0.04
                ready, _, _ = select.select([sys.stdin], [], [], wait_sec)
                if ready:
                    chunks: List[str] = []
                    while True:
                        chunk_b = os.read(fd, 65536)
                        if not chunk_b:
                            break
                        chunks.append(chunk_b.decode("utf-8", errors="replace"))
                        more, _, _ = select.select([sys.stdin], [], [], 0.02)
                        if not more:
                            break
                    piped_stdin = "".join(chunks)
        except Exception:
            piped_stdin = ""
    if piped_stdin.strip():
        if len(piped_stdin) > 2500:
            entry = get_artifact_store().ingest_text(piped_stdin, name="stdin.txt")
            user_q = f"{entry.descriptor}\n\n{user_q or 'Analyze the piped stdin input.'}"
        else:
            user_q = f"Input:\n```\n{piped_stdin.strip()}\n```\n\n{user_q or 'Process the input above.'}"

    if not user_q:
        sys.stderr.write("Error: No query provided. Pass a prompt or pipe stdin.\n")
        return 1

    parsed_schema: Optional[Dict[str, Any]] = None
    if schema_arg:
        if os.path.isfile(schema_arg):
            with open(schema_arg, "r", encoding="utf-8") as sf:
                parsed_schema = json.load(sf)
        else:
            parsed_schema = json.loads(schema_arg)

    kernel = get_kernel()
    forced = ExecutionMode.PLAN_DAG if plan_mode else None

    # Stream directly to stdout unless pure JSON output or schema enforcement is requested
    if output_json or parsed_schema is not None:
        outcome = kernel.run_turn(
            user_query=user_q,
            session_id=session_id,
            attachments=files,
            pinned_agent=agent,
            forced_mode=forced,
            json_schema=parsed_schema,
            max_tokens=max_tokens,
        )
        if parsed_schema is not None and not output_json:
            if outcome.structured_output is not None:
                print(json.dumps(outcome.structured_output, indent=2, ensure_ascii=False))
            else:
                print(outcome.final_text)
        else:
            print(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
        return 0 if not outcome.error else 1

    exit_code = 0
    for ev in kernel.stream_turn(
        user_query=user_q,
        session_id=session_id,
        attachments=files,
        pinned_agent=agent,
        forced_mode=forced,
        max_tokens=max_tokens,
    ):
        if ev.event_type == EventType.TOKEN_DELTA:
            sys.stdout.write(ev.payload.get("text", ""))
            sys.stdout.flush()
        elif ev.event_type == EventType.RUN_ERROR:
            sys.stderr.write(f"\nError: {ev.payload.get('error')}\n")
            exit_code = 1
    sys.stdout.write("\n")
    sys.stdout.flush()
    return exit_code


def run_yaml_workflow(workflow_path: str) -> int:
    import yaml

    if not os.path.isfile(workflow_path):
        sys.stderr.write(f"Workflow file not found: {workflow_path}\n")
        return 1
    with open(workflow_path, "r", encoding="utf-8") as f:
        wf = yaml.safe_load(f) or {}

    steps = wf.get("steps") or []
    sid = str(wf.get("session_id") or f"wf_{uuid.uuid4().hex[:8]}")
    if wf.get("profile"):
        set_active_profile(str(wf["profile"]))

    kernel = get_kernel()
    results: List[Dict[str, Any]] = []
    for idx, st in enumerate(steps, start=1):
        prompt = str(st.get("prompt") or st.get("query") or "")
        agent = st.get("agent")
        files = st.get("files") or []
        print(f"=== [Step {idx}/{len(steps)}] {st.get('name', agent or 'turn')} ===")
        out = kernel.run_turn(
            user_query=prompt,
            session_id=sid,
            attachments=files,
            pinned_agent=agent,
        )
        print(out.final_text)
        print()
        results.append(out.to_dict())

    if wf.get("output_json"):
        with open(str(wf["output_json"]), "w", encoding="utf-8") as out_f:
            json.dump(results, out_f, indent=2, ensure_ascii=False)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args_list = list(argv if argv is not None else sys.argv[1:])
    if not args_list:
        return run_interactive_chat()

    parser = argparse.ArgumentParser(
        prog="slm",
        description="SLMAgents 10-Plane Pure-CPU Interactive Agent Harness",
    )
    parser.add_argument("--profile", choices=["lite", "balanced", "power"], help="CPU RAM/Model profile override")
    parser.add_argument("--backend", choices=["auto", "gguf", "onnx"], help="Preferred CPU inference engine")
    parser.add_argument("--offline", action="store_true", help="Strict 100% air-gapped offline mode")

    sub = parser.add_subparsers(dest="subcommand")

    # chat
    p_chat = sub.add_parser("chat", help="Launch interactive Rich TUI/REPL session")
    p_chat.add_argument("--session", default=None, help="Resume or name a specific session ID")
    p_chat.add_argument("--agent", default=None, help="Pin an initial specialist agent")

    # ask
    p_ask = sub.add_parser("ask", help="Headless single-shot or Unix stdin-pipe query")
    p_ask.add_argument("query", nargs="*", help="Prompt text")
    p_ask.add_argument("-f", "--file", action="append", default=[], help="Attach file(s) (PDF/CSV/SQLite/Image/Audio/Code)")
    p_ask.add_argument("-a", "--agent", default=None, help="Pin specialist agent (e.g. slm_text_to_sql)")
    p_ask.add_argument("--plan", action="store_true", help="Force multi-agent PLAN_DAG execution")
    p_ask.add_argument("--schema", default=None, help="JSON Schema string or file path for constrained output")
    p_ask.add_argument("--json", action="store_true", help="Output full structured TurnOutcome JSON")
    p_ask.add_argument("--session", default="cli_headless", help="Session ID for multi-shot CLI continuity")
    p_ask.add_argument("--max-tokens", type=int, default=1024)

    # run
    p_run = sub.add_parser("run", help="Execute a declarative YAML multi-step workflow")
    p_run.add_argument("workflow", help="Path to workflow.yaml")

    # serve
    p_serve = sub.add_parser("serve", help="Start Offline Web AI Studio & OpenAI /v1 API server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7860)

    # app
    p_app = sub.add_parser("app", help="Launch Native Cross-Platform Desktop App Window (macOS / Windows / Linux)")
    p_app.add_argument("--host", default="127.0.0.1")
    p_app.add_argument("--port", type=int, default=None)
    p_app.add_argument("--skip-download", action="store_true")

    # doctor
    p_doc = sub.add_parser("doctor", help="Inspect CPU topology, SIMD, dependencies & quantized models")
    p_doc.add_argument("--json", action="store_true")

    # bench
    p_bench = sub.add_parser("bench", help="Run Pure-CPU tok/s, TTFT, GBNF JSON & RAM benchmark")
    p_bench.add_argument("--json", action="store_true")

    # models
    p_mod = sub.add_parser("models", help="Manage quantized CPU model weights & prune FP32 duplicates")
    p_mod.add_argument("--download", choices=["lite", "balanced", "power", "all"], default=None)
    p_mod.add_argument("--check-dups", action="store_true")
    p_mod.add_argument("--prune-fp32", action="store_true")

    # mcp-serve
    sub.add_parser("mcp-serve", help="Expose SLMAgents CPU Harness over stdio Model Context Protocol")

    parsed = parser.parse_args(args_list)

    cfg = get_config()
    if parsed.offline:
        cfg.offline_only = True
    if parsed.profile or parsed.backend:
        set_active_profile(parsed.profile or cfg.profile.value, preferred_backend=parsed.backend)

    cmd = parsed.subcommand
    if cmd in (None, "chat"):
        return run_interactive_chat(
            session_id=getattr(parsed, "session", None),
            initial_agent=getattr(parsed, "agent", None),
        )
    if cmd == "ask":
        return run_headless_ask(
            query_parts=parsed.query,
            files=parsed.file,
            agent=parsed.agent,
            plan_mode=parsed.plan,
            schema_arg=parsed.schema,
            output_json=parsed.json,
            session_id=parsed.session,
            max_tokens=parsed.max_tokens,
        )
    if cmd == "run":
        return run_yaml_workflow(parsed.workflow)
    if cmd == "doctor":
        return render_doctor_cli(as_json=parsed.json)
    if cmd == "bench":
        return render_bench_cli(as_json=parsed.json)
    if cmd == "models":
        import download_models

        if parsed.check_dups or parsed.prune_fp32:
            download_models.prune_fp32_duplicates(cfg.models_dir, apply=parsed.prune_fp32)
            return 0
        if parsed.download:
            download_models.download_profile_models(profile=parsed.download, models_dir=cfg.models_dir)
            return 0
        return render_doctor_cli(as_json=False)
    if cmd == "mcp-serve":
        from .mcp_server import serve_mcp_stdio

        serve_mcp_stdio()
        return 0
    if cmd == "serve":
        import uvicorn

        print(f"Launching SLMAgents Pure-CPU AI Studio & OpenAI /v1 API at http://{parsed.host}:{parsed.port}/chat.html")
        uvicorn.run("main:app", host=parsed.host, port=parsed.port, log_level="info")
        return 0
    if cmd == "app":
        from desktop_app import run_desktop_app

        return run_desktop_app(
            host=parsed.host,
            port=parsed.port,
            profile=parsed.profile,
            skip_download=parsed.skip_download,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
