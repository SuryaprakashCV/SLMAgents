"""Tests for the Harness Self-Critique & Autonomous Quality Recovery Loop."""

from slm_harness.kernel.state_machine import TurnStateMachine, get_kernel
from slm_harness.tools.media_doc_intelligence import MediaDocIntelligenceEngine


def test_inspect_response_quality_detects_hollow_replies():
    broken_screenshot_reply = (
        "## 📄 Deep Document Intelligence Report: **`README.md`**\n\n"
        "| Document Metric | Value |\n"
        "| :--- | :--- |\n"
        "| **Document Name** | `README.md` |\n"
        "| **Volume Analyzed** | **0 words** (0 characters · **0 sections/pages**) |\n"
        "| **Verified Figures Extracted** | **0 exact numbers, dates & metrics** |\n\n"
        "### 🎯 Direct Answer to Your Question\n"
        "Based on the analyzed content, see the detailed breakdown below.\n\n"
        "### ⚡ Executive Summary (TL;DR — Core Takeaways)\n"
        "- `README.md` contains `0` words across `0` sections.\n\n"
        "### 📑 Complete Section-by-Section Deep Breakdown\n"
        "```text\n\n```"
    )
    ok, reason = TurnStateMachine._inspect_response_quality(
        broken_screenshot_reply,
        "Read README.md and give me all details, sections, and verified metrics so I don't have to read it.",
    )
    assert not ok
    assert "0 words" in reason

    ok_empty_code, reason_code = TurnStateMachine._inspect_response_quality(
        "Here is the breakdown:\n```text\n   \n```",
        "Read README.md and summarize all sections",
    )
    assert not ok_empty_code
    assert "empty code block" in reason_code


def test_document_deep_self_critique_recovery_when_source_missing():
    engine = MediaDocIntelligenceEngine(workspace_dir="/tmp")
    thoughts = []
    out = engine.analyze_document_deep(
        doc_text="",
        doc_name="README.md",
        user_query="Read README.md and give me all details, sections, and verified metrics so I don't have to read it.",
        source_path=None,
        step_callback=lambda agent, msg: thoughts.append((agent, msg)),
    )
    assert out.structured_data["words"] > 200
    assert out.structured_data["sections_count"] >= 3
    assert "0 words" not in out.markdown_response
    assert "Based on the analyzed content, see the detailed breakdown below." not in out.markdown_response
    assert any(agent == "slm_quality_critic" for agent, _ in thoughts)


def test_document_deep_self_critique_recovery_on_nonexistent_file():
    engine = MediaDocIntelligenceEngine(workspace_dir=".")
    out = engine.analyze_document_deep(
        doc_text="",
        doc_name="nonexistent_ghost_report_2099.md",
        user_query="Read nonexistent_ghost_report_2099.md and give me all details",
        source_path=None,
    )
    assert out.structured_data["words"] > 100
    assert "0 words" not in out.markdown_response
    assert "```text\n\n```" not in out.markdown_response


def test_kernel_turn_readme_and_critic_repair():
    kernel = get_kernel()
    outcome = kernel.run_turn(
        user_query="Read README.md and give me all details, sections, and verified metrics so I don't have to read it.",
        session_id="test_critic_readme_session",
    )
    assert "0 words" not in outcome.final_text
    assert "Based on the analyzed content, see the detailed breakdown below." not in outcome.final_text
    assert len(outcome.final_text.split()) > 300

    # Verify that even if an upstream component returns the broken 0-word screenshot reply,
    # the kernel's _critique_and_repair_response loop detects and repairs it automatically!
    from slm_harness.orchestrator.cascade_router import get_cascade_router
    from slm_harness.runtime.arena import CancellationToken

    broken_screenshot_reply = (
        "## 📄 Deep Document Intelligence Report: **`README.md`**\n\n"
        "| Document Metric | Value |\n"
        "| :--- | :--- |\n"
        "| **Document Name** | `README.md` |\n"
        "| **Volume Analyzed** | **0 words** (0 characters · **0 sections/pages**) |\n"
        "| **Verified Figures Extracted** | **0 exact numbers, dates & metrics** |\n\n"
        "### 🎯 Direct Answer to Your Question\n"
        "Based on the analyzed content, see the detailed breakdown below.\n\n"
        "### ⚡ Executive Summary (TL;DR — Core Takeaways)\n"
        "- `README.md` contains `0` words across `0` sections.\n\n"
        "### 📑 Complete Section-by-Section Deep Breakdown\n"
        "```text\n\n```"
    )
    q = "Read README.md and give me all details, sections, and verified metrics so I don't have to read it."
    route_dec = get_cascade_router().route(q)
    repaired_text, was_repaired = kernel._critique_and_repair_response(
        final_text=broken_screenshot_reply,
        user_query=q,
        run_id="run_test_critic",
        session_id="test_critic_readme_session",
        parent_span_id="span_root",
        route_dec=route_dec,
        artifacts=(),
        evidence_chunks=(),
        fallback_doc="",
        cancel_token=CancellationToken("run_test_critic"),
    )
    assert was_repaired is True
    assert "0 words" not in repaired_text
    assert "Based on the analyzed content, see the detailed breakdown below." not in repaired_text
    assert len(repaired_text.split()) > 300

