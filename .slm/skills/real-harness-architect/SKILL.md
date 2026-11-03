---
name: real-harness-architect
title: "Real Harness Autonomous Architect & Self-Review Engine"
description: "Claude-Code-grade Harness skill: formulates loophole-free architectural plans, clarifies ambiguities with interactive options, performs live web research, executes sandboxed verification, manages multi-threaded CPU memory, and runs automated self-review & self-improvement."
role: coding
agent: slm_code_interpreter
tone: tech
output_formats: [py, md, json]
triggers: [real harness, harness architect, architecture plan, loophole free, self review]
tags: [harness, architecture, coding, planning, verification, memory-management]
enabled: true
source: built-in
version: 2
created_at: 2026-11-04T00:35:00Z
---

# Real Harness Autonomous Architect & Self-Review Engine

## Purpose
Equips SLMAgents 2.0 with a true Claude-Code-grade Agentic Harness workflow:
1. **Zero Misrouting & Full User Trust**: Coding, algorithm, and system design requests always route directly to the verified coding & architecture engine—never to email or unrelated document templates.
2. **Adaptive Reasoning Effort (`Low` / `Medium` / `High`)**:
   - **Low (`⚡ Fast`)**: Sub-second TTFT using lightweight ONNX models (`0.8B` / `1.5B`) for rapid direct answers.
   - **Medium (`🧠 Balanced Plan`)**: Structured upfront Execution Plan + deterministic AST & sandbox execution verification (`3B ONNX` / `4B GGUF`).
   - **High (`🔬 Deep Research & Self-Review`)**: Live web research, comprehensive loophole-free architecture plan, isolated sandbox execution, and an automated **Self-Review & Self-Improvement Critic** (`4B` / `8B GGUF` calibrated to available RAM).
3. **Interactive Clarification with Selectable Options**:
   - Whenever requirements have multiple valid architectural paths, surface clickable option pills so the user can steer the plan with a single click.
4. **Loophole-Free Coding & Self-Improvement**:
   - Every coding solution includes:
     1. **Architecture & Execution Plan** (Time/Space complexity, data structures, module invariants)
     2. **Edge-Case & Loophole Guards** ($n \le 0$, empty inputs, overflow/recursion limits, type safety)
     3. **Production-Grade Implementation** (PEP-8, full type annotations, docstrings, zero placeholders)
     4. **Sandbox Execution & Self-Review** (Executed in isolated Python subprocess + critic verification)
5. **Active Memory Arena & Multi-Threaded Scheduler**:
   - Proactively manages RAM watermarks (`MemoryArena`), schedules work across non-blocking `fast`, `compute`, and `background` thread pools, and auto-downloads missing specialist models fitting available RAM and CPU cores.
