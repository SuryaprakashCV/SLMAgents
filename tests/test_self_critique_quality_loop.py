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


def test_web_personalization_query_returns_security_notice_and_download_links():
    kernel = get_kernel()
    outcome = kernel.run_turn(
        user_query="i a=cant see personalization option here",
        session_id="test_web_personalize_notice",
        client_surface="web",
    )
    assert "security and privacy reasons" in outcome.final_text.lower()
    assert "/api/installers/download/macos" in outcome.final_text
    assert "/api/installers/download/windows" in outcome.final_text
    assert "/api/installers/download/linux" in outcome.final_text
    assert "Personalization Applied:" not in outcome.final_text


def test_who_built_you_returns_suryaprakash():
    from slm_harness.orchestrator.cascade_router import get_cascade_router
    from slm_harness.runtime.arena import CancellationToken

    kernel = get_kernel()
    for prompt in ("who built you", "who created you", "who made you", "who is your creator?"):
        outcome = kernel.run_turn(
            user_query=prompt,
            session_id="test_creator_identity",
            client_surface="web",
        )
        assert "suryaprakash" in outcome.final_text.lower(), f"Missing Suryaprakash for prompt={prompt!r}: {outcome.final_text}"

    # Verify quality critic repairs any non-Suryaprakash response to "who built you"
    route_dec = get_cascade_router().route("who built you")
    repaired_text, was_repaired = kernel._critique_and_repair_response(
        final_text="I was built by Alibaba Cloud.",
        user_query="who built you",
        run_id="run_test_creator_critic",
        session_id="test_creator_identity",
        parent_span_id="span_root",
        route_dec=route_dec,
        artifacts=(),
        evidence_chunks=(),
        fallback_doc="",
        cancel_token=CancellationToken("run_test_creator_critic"),
    )
    assert was_repaired is True
    assert "suryaprakash" in repaired_text.lower()


def test_local_project_build_creates_files_runs_tests_and_completes_project():
    import os

    kernel = get_kernel()
    # 1. Confirming a build plan creates all files on disk, runs tests, and returns live URL + ZIP
    outcome = kernel.run_turn(
        user_query="i want to build a website for test suitw which automaticaly test and and evaluate any applications..",
        session_id="test_local_build_session",
        clarification_choice="🧪 Go ahead + include an expanded test & edge-case suite",
        client_surface="web",
    )
    assert "Local Project Built & Verified on Disk" in outcome.final_text
    assert "http://localhost:8000/projects/autotest_hub/index.html" in outcome.final_text
    assert "TESTS PASSED" in outcome.final_text
    assert "this is a simulated link" not in outcome.final_text.lower()

    proj_dir = outcome.structured_output["project_dir"]
    for expected_file in ("index.html", "styles.css", "app.js", "engine.py", "server.py", "tests/test_project.py", "README.md"):
        assert os.path.isfile(os.path.join(proj_dir, expected_file)), f"Missing {expected_file} in {proj_dir}"

    # 2. Follow-up "build it locally" also builds and completes the project on disk
    outcome_followup = kernel.run_turn(
        user_query="build it locally",
        session_id="test_local_build_session",
        client_surface="web",
    )
    assert "Local Project Built & Verified on Disk" in outcome_followup.final_text
    assert "http://localhost:8000/projects/autotest_hub/index.html" in outcome_followup.final_text

    # 3. Verify plan for "build an interviwer app" has zero <slug> or ... placeholders
    plan_outcome = kernel.run_turn(
        user_query="build an interviwer app",
        session_id="test_interviewer_session",
        client_surface="desktop",
    )
    assert "<slug>" not in plan_outcome.final_text
    assert "projects/..." not in plan_outcome.final_text
    assert "AI Interviewer Studio" in plan_outcome.final_text

    # 4. Confirming "build an interviwer app" builds projects/interviewer_app with zero pre-filled values
    build_interviewer = kernel.run_turn(
        user_query="build an interviwer app",
        session_id="test_interviewer_session",
        clarification_choice="✅ Looks great — go ahead and build it!",
        client_surface="desktop",
    )
    assert "http://localhost:8000/projects/interviewer_app/index.html" in build_interviewer.final_text
    assert "TESTS PASSED" in build_interviewer.final_text

    # 5. Building resume builder app creates projects/interact_user_resume_app with zero pre-filled values
    build_resume = kernel.run_turn(
        user_query="build an app for mr which can interact with user and build a resume for them",
        session_id="test_resume_session",
        clarification_choice="it should have a chat interface and ask iuser about their details..",
        client_surface="desktop",
    )
    assert "http://localhost:8000/projects/interact_user_resume_app/index.html" in build_resume.final_text
    assert "TESTS PASSED" in build_resume.final_text

    # 6. Replying "Want me to generate the full source code (HTML, JS, Python) for this project? yes"
    # or "create project which is there in my previous chat" builds the actual project from history (never Fibonacci!)
    outcome_gen_src = kernel.run_turn(
        user_query="Want me to generate the full source code (HTML, JS, Python) for this project? yes",
        session_id="test_local_build_session",
        client_surface="desktop",
    )
    assert "Local Project Built & Verified on Disk" in outcome_gen_src.final_text
    assert "http://localhost:8000/projects/autotest_hub/index.html" in outcome_gen_src.final_text
    assert "fibonacci" not in outcome_gen_src.final_text.lower()

    outcome_prev_chat = kernel.run_turn(
        user_query="create project which is there in my previous chat",
        session_id="test_interviewer_session",
        client_surface="desktop",
    )
    assert "Local Project Built & Verified on Disk" in outcome_prev_chat.final_text
    assert "http://localhost:8000/projects/interviewer_app/index.html" in outcome_prev_chat.final_text
    assert "fibonacci" not in outcome_prev_chat.final_text.lower()


def test_project_builder_custom_folder_and_permissions() -> None:
    import os
    import tempfile
    from slm_harness.governance.policy import get_folder_permission_manager
    from slm_harness.tools.project_builder import LocalProjectBuilder

    perm_mgr = get_folder_permission_manager()
    with tempfile.TemporaryDirectory() as tmp_parent:
        custom_folder = os.path.join(tmp_parent, "user_chosen_project_folder")

        # 1. Before granting permission, check_permission requires consent
        check0 = perm_mgr.check_permission(custom_folder, operation="all")
        assert check0["permitted"] is False
        assert check0["requires_consent"] is True

        # 2. Attempting to create/edit/delete/execute without permission returns permission_required
        blocked = LocalProjectBuilder.perform_permitted_folder_operation(
            folder_path=custom_folder,
            operation="create",
            rel_path="hello.txt",
            content="blocked",
        )
        assert blocked["status"] == "permission_required"
        assert blocked["requires_consent"] is True

        # 3. User grants all permissions under custom_folder (create, edit, delete, run, execute)
        granted = perm_mgr.grant_permission(
            folder_path=custom_folder,
            permissions={
                "create": True,
                "edit": True,
                "delete": True,
                "run": True,
                "execute": True,
                "full_access": True,
            },
            project_name="Custom Folder Project",
            project_slug="custom_folder_proj",
            set_active=False,
        )
        assert granted["permitted"] is True
        assert granted["full_access"] is True

        # 4. Build a project inside the chosen custom_folder
        builder = LocalProjectBuilder()
        outcome = builder.build_project(
            user_query="build an interviewer app",
            target_folder=custom_folder,
            custom_project_name="Custom Folder Interviewer",
            grant_permission=True,
        )
        assert os.path.isdir(outcome.project_dir)
        assert os.path.isfile(os.path.join(outcome.project_dir, "index.html"))
        assert outcome.test_passed is True

        # 5. Create, Read, Edit, Execute, and Delete under the permitted folder all succeed
        op_create = LocalProjectBuilder.perform_permitted_folder_operation(
            folder_path=outcome.project_dir,
            operation="create",
            rel_path="custom_notes.txt",
            content="initial content",
        )
        assert op_create["status"] == "ok"

        op_edit = LocalProjectBuilder.perform_permitted_folder_operation(
            folder_path=outcome.project_dir,
            operation="edit",
            rel_path="custom_notes.txt",
            content="updated content",
        )
        assert op_edit["status"] == "ok"

        op_read = LocalProjectBuilder.perform_permitted_folder_operation(
            folder_path=outcome.project_dir,
            operation="read",
            rel_path="custom_notes.txt",
        )
        assert op_read["status"] == "ok"
        assert op_read["content"] == "updated content"

        op_exec = LocalProjectBuilder.perform_permitted_folder_operation(
            folder_path=outcome.project_dir,
            operation="execute",
            command="python3 -c \"print('PERM_EXEC_OK')\"",
        )
        assert op_exec["status"] == "ok"
        assert "PERM_EXEC_OK" in op_exec["stdout"]

        op_del = LocalProjectBuilder.perform_permitted_folder_operation(
            folder_path=outcome.project_dir,
            operation="delete",
            rel_path="custom_notes.txt",
        )
        assert op_del["status"] == "ok"
        assert not os.path.exists(os.path.join(outcome.project_dir, "custom_notes.txt"))

        # 6. Revoking permission blocks operations again
        perm_mgr.revoke_permission(outcome.project_dir)
        perm_mgr.revoke_permission(custom_folder)
        check_after = perm_mgr.check_permission(custom_folder, operation="all")
        assert check_after["permitted"] is False


if __name__ == "__main__":
    test_inspect_response_quality_detects_hollow_replies()
    test_document_deep_self_critique_recovery_when_source_missing()
    test_document_deep_self_critique_recovery_on_nonexistent_file()
    test_kernel_turn_readme_and_critic_repair()
    test_web_personalization_query_returns_security_notice_and_download_links()
    test_who_built_you_returns_suryaprakash()
    test_local_project_build_creates_files_runs_tests_and_completes_project()
    test_project_builder_custom_folder_and_permissions()
    print("ALL 8 SELF-CRITIQUE, WEB PERSONALIZATION, CREATOR IDENTITY, PROJECT BUILDER & FOLDER PERMISSION TESTS PASSED!")



