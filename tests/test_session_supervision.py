"""Regression tests for extending the current session aggregate."""

import pytest
from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.session_manager import (
    SessionManager,
    SessionStatus,
    agent_provider_blocker,
    build_agent_command,
    extract_agent_report,
    get_session_manager,
)


def test_session_supervision_fields_and_criteria_are_durable():
    manager = SessionManager()
    created = manager.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Deliver a supervised outcome",
        acceptance_criteria=["Tests pass", "Browser behavior is verified"],
        policy={"allow_network": False},
        budget={"minutes": 30},
    )

    manager._sessions.clear()
    loaded = manager.get(created.id)

    assert loaded is not None
    assert loaded.goal == "Deliver a supervised outcome"
    assert loaded.acceptance_criteria == ["Tests pass", "Browser behavior is verified"]
    assert loaded.control_owner == "none"
    assert loaded.policy == {"allow_network": False}
    assert loaded.budget == {"minutes": 30}

    criteria = manager._db().execute(
        "SELECT criterion, status FROM session_criteria WHERE session_id = ? ORDER BY criterion_index",
        (created.id,),
    ).fetchall()
    assert [(row["criterion"], row["status"]) for row in criteria] == [
        ("Tests pass", "unevaluated"),
        ("Browser behavior is verified", "unevaluated"),
    ]


def test_supervised_success_enters_verification_instead_of_claiming_completion():
    manager = SessionManager()
    session = manager.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Verify before completion",
        metadata={"supervised": True, "review_mode": "implementation"},
    )
    assert manager.successful_execution_status(session) == SessionStatus.VERIFYING


def test_supervised_completion_is_evidence_gated():
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Do not complete on an agent claim",
        metadata={"supervised": True, "review_mode": "implementation"},
        acceptance_criteria=["Independent checks pass"],
    )
    token, _ = get_auth_manager().create_token("session-gate-test", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{session.id}/complete",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert "Git evidence" in response.json()["detail"]
    assert "passing verification evidence" in response.json()["detail"]
    assert "resolved acceptance criteria" in response.json()["detail"]


def test_codex_final_report_is_extracted_without_cli_noise():
    transcript = "reading files\nmore tool output\ncodex\n# Recommendation\n\nUse the dashboard data.\ntokens used\n1,234\n__RDC_EXIT:0\n"
    assert extract_agent_report(transcript, "codex") == "# Recommendation\n\nUse the dashboard data."
    assert extract_agent_report(transcript, "orchestrated") == "# Recommendation\n\nUse the dashboard data."
    marked = "tool chatter\n__RDC_FINAL_BEGIN__\nFinal answer only.\n__RDC_FINAL_END__\n__RDC_EXIT:0"
    assert extract_agent_report(marked, "orchestrated") == "Final answer only."


def test_continuation_inherits_parent_context_and_lineage():
    manager = get_session_manager()
    parent = manager.create_session("test-channel", "test-project", "Evaluate the homepage", metadata={"supervised": True})
    manager.update_status(parent.id, SessionStatus.COMPLETED, output_summary="Build a dashboard endpoint and summary cards.")
    token, _ = get_auth_manager().create_token("continuation-test", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{parent.id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Implement the approved dashboard plan", "provider": "orchestrated"},
    )
    assert response.status_code == 200
    child = manager.get(response.json()["id"])
    assert child is not None
    assert child.metadata["parent_session_id"] == parent.id
    assert child.metadata["review_mode"] == "implementation"
    assert response.json()["review_mode"] == "implementation"
    assert child.metadata["inherited_context"]["parent_result"] == "Build a dashboard endpoint and summary cards."
    repeated = TestClient(app).post(
        f"/sessions/{parent.id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Accidentally create a sibling"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == child.id


def test_instruction_failure_keeps_the_review(monkeypatch):
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="instruct-channel",
        project="instruct-project",
        description="Review the homepage",
        agent_provider="diagnostic",
        metadata={"supervised": True},
    )
    manager.update_status(session.id, SessionStatus.VERIFYING, output_summary="Ready for review")

    def boom(*_args, **_kwargs):
        raise RuntimeError("not ready")

    monkeypatch.setattr(
        "remote_dev_ctrl.server.supervised_sessions.start_supervised_session",
        boom,
    )
    token, _ = get_auth_manager().create_token("instruct-fail", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{session.id}/instructions",
        headers={"Authorization": f"Bearer {token}"},
        json={"instruction": "Also fix the footer"},
    )
    assert response.status_code == 409
    fresh = manager.get(session.id)
    assert fresh is not None
    assert fresh.status == SessionStatus.VERIFYING
    assert fresh.output_summary == "Ready for review"
    assert "followup_instructions" not in (fresh.metadata or {})


def test_execution_prompt_reconstructs_same_session_followups():
    from remote_dev_ctrl.server.app import _session_execution_prompt

    session = get_session_manager().create_session(
        "test-channel",
        "test-project",
        "Implement the dashboard",
        metadata={
            "supervised": True,
            "followup_instructions": [
                {
                    "prior_result": "Dashboard implementation and tests completed.",
                    "instruction": "Commit the changes and request approval before pushing.",
                }
            ],
        },
    )
    prompt = _session_execution_prompt(session)
    assert "continued conversation within the same RDC Session" in prompt
    assert "Dashboard implementation and tests completed" in prompt
    assert "Commit the changes" in prompt


def test_terminal_approval_is_durable_and_decided_idempotently():
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Request approval",
        metadata={"supervised": True},
    )
    approval_id = manager.request_approval(session, "Proceed with deployment?", "missing-terminal")
    assert manager.request_approval(session, "Duplicate prompt", "missing-terminal") == approval_id

    token, _ = get_auth_manager().create_token("approval-test", Role.ADMIN)
    client = TestClient(app)
    response = client.post(
        f"/sessions/{session.id}/approvals/{approval_id}/decide",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "rejected", "reason": "Not ready"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    repeated = client.post(
        f"/sessions/{session.id}/approvals/{approval_id}/decide",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "approved"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "rejected"


def test_provider_selection_builds_the_selected_agent_command():
    cursor_command = build_agent_command("cursor", "Fix the homepage")
    assert "cursor-agent -p --trust" in cursor_command
    assert "claude" not in cursor_command
    assert "diagnostic session" in build_agent_command("diagnostic", "No writes")
    codex_command = build_agent_command("codex", "Fix the homepage")
    assert "codex --ask-for-approval never exec --sandbox workspace-write" in codex_command
    with pytest.raises(ValueError, match="Unsupported session provider: gemini"):
        build_agent_command("gemini", "Review data")


def test_cursor_provider_readiness_is_checked_before_terminal_creation(monkeypatch):
    class Result:
        returncode = 1

    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_manager.shutil.which",
        lambda name: "/usr/bin/cursor-agent" if name == "cursor-agent" else None,
    )
    monkeypatch.setattr("remote_dev_ctrl.server.session_manager.subprocess.run", lambda *args, **kwargs: Result())
    assert "not authenticated" in agent_provider_blocker("cursor")
    assert agent_provider_blocker("diagnostic") is None


def test_control_can_be_returned_without_guidance_and_terminal_states_clear_owner():
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Exercise control handoff",
        metadata={"supervised": True},
    )
    session.status = SessionStatus.RUNNING
    session.control_owner = "human"
    manager._persist(session)

    token, _ = get_auth_manager().create_token("control-return-test", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{session.id}/return-control",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert response.status_code == 200
    assert response.json()["control_owner"] == "agent"

    manager.update_status(session.id, SessionStatus.CANCELLED)
    assert manager.get(session.id).control_owner == "none"
    rejected = TestClient(app).post(
        f"/sessions/{session.id}/return-control",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert rejected.status_code == 409


def test_cancelled_outcome_can_restore_review():
    manager = get_session_manager()
    session = manager.create_session("test-channel", "test-project", "Deliver result", metadata={"supervised": True})
    manager.update_status(session.id, SessionStatus.CANCELLED, output_summary="A valid delivered outcome")
    token, _ = get_auth_manager().create_token("restore-review-test", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{session.id}/restore-review",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "verifying"


def test_passing_verification_satisfies_matching_criterion(monkeypatch, tmp_path):
    manager = get_session_manager()
    session = manager.create_session(
        "test-channel", "test-project", "Verify result",
        metadata={"supervised": True}, acceptance_criteria=["Relevant verification passes", "Human accepts outcome"],
    )

    class Result:
        returncode = 0
        stdout = "2 passed"
        stderr = ""

    monkeypatch.setattr("remote_dev_ctrl.server.app._session_project_path", lambda project: tmp_path)
    monkeypatch.setattr("remote_dev_ctrl.server.app.subprocess.run", lambda *args, **kwargs: Result())
    token, _ = get_auth_manager().create_token("auto-criterion-test", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{session.id}/evidence/verification",
        headers={"Authorization": f"Bearer {token}"},
        json={"command": "pytest"},
    )
    assert response.status_code == 200
    rows = manager._db().execute(
        "SELECT criterion, status FROM session_criteria WHERE session_id = ? ORDER BY criterion_index", (session.id,),
    ).fetchall()
    assert [(row["criterion"], row["status"]) for row in rows] == [
        ("Relevant verification passes", "passed"), ("Human accepts outcome", "unevaluated"),
    ]


def test_product_media_can_be_attached_as_session_evidence(monkeypatch, tmp_path):
    manager = get_session_manager()
    session = manager.create_session("test-channel", "test-project", "Review browser behavior")
    monkeypatch.setattr("remote_dev_ctrl.server.app.get_rdc_home", lambda: tmp_path)
    token, _ = get_auth_manager().create_token("media-evidence-test", Role.ADMIN)
    client = TestClient(app)
    response = client.post(
        f"/sessions/{session.id}/evidence/media",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("homepage.png", b"png-data", "image/png")},
        data={"note": "Homepage dashboard renders"},
    )
    assert response.status_code == 200
    assert response.json()["evidence_type"] == "media"
    evidence_id = response.json()["id"]
    downloaded = client.get(
        f"/sessions/{session.id}/evidence/{evidence_id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"png-data"
    updated = client.patch(
        f"/sessions/{session.id}/evidence/{evidence_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Updated dashboard screenshot", "note": "Updated dashboard screenshot"},
    )
    assert updated.status_code == 200
    deleted = client.delete(
        f"/sessions/{session.id}/evidence/{evidence_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert deleted.status_code == 200
    assert not list((tmp_path / "session-evidence" / session.id).iterdir())


def test_operator_can_complete_with_audited_review_exception():
    manager = get_session_manager()
    session = manager.create_session(
        "test-channel", "test-project", "Accept known risk",
        metadata={"supervised": True, "review_mode": "implementation"},
        acceptance_criteria=["Verification passes"],
    )
    session.status = SessionStatus.VERIFYING
    manager._persist(session)
    token, _ = get_auth_manager().create_token("review-override-test", Role.ADMIN)
    response = TestClient(app).post(
        f"/sessions/{session.id}/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={"override": True, "note": "Verified manually in the deployed preview"},
    )
    assert response.status_code == 200
    restored = manager.get(session.id)
    assert restored.status == SessionStatus.COMPLETED
    assert "Verified manually" in restored.unresolved_risks[-1]


def test_unified_driver_goal_run_and_native_resume_commands():
    from remote_dev_ctrl.server.agent_registry import resolve_interactive_terminal_command

    claude_run = build_agent_command("claude", "Audit the layout")
    assert "claude -p" in claude_run
    assert "__RDC_FINAL_BEGIN__" in claude_run

    claude_resume = build_agent_command(
        "claude",
        "Now add tests",
        resume=True,
        fallback_description="Full reconstructed history: Audit the layout -> Now add tests",
    )
    assert "claude -p --continue" in claude_resume
    assert "Full reconstructed history" in claude_resume

    codex_resume = build_agent_command(
        "codex",
        "Now add tests",
        resume=True,
        fallback_description="Full reconstructed history",
    )
    assert "exec resume --last" in codex_resume
    assert "exec --sandbox workspace-write" in codex_resume

    term_cmd = resolve_interactive_terminal_command("rdc-launch cursor-agent", "/tmp", "demo", mode="resume:abc-123")
    assert term_cmd == "cursor-agent --resume=abc-123"


def test_custom_wrapper_crud_in_agent_registry(monkeypatch, tmp_path):
    monkeypatch.setattr("remote_dev_ctrl.server.agent_registry.get_rdc_home", lambda: tmp_path)
    token, _ = get_auth_manager().create_token("wrapper-crud-test", Role.ADMIN)
    client = TestClient(app)

    put_resp = client.put(
        "/settings/agent-providers/wrappers/grok",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "label": "Grok CLI",
            "driver": "grok",
            "executable": "grok",
            "terminal_command": "grok",
            "supports_sessions": True,
            "show_in_terminal_menu": True,
        },
    )
    assert put_resp.status_code == 200
    provider_ids = [p["id"] for p in put_resp.json()["providers"]]
    assert "grok" in provider_ids

    grok_cmd = build_agent_command("grok", "Optimize the query")
    assert "grok -p 'Optimize the query'" in grok_cmd
    assert "__RDC_FINAL_BEGIN__" in grok_cmd

    del_resp = client.delete(
        "/settings/agent-providers/wrappers/grok",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200
    assert "grok" not in [p["id"] for p in del_resp.json()["providers"]]


def test_followup_run_keeps_its_own_diff_beside_the_review_artifact(monkeypatch, tmp_path):
    import subprocess as sp

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    sp.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
    sp.run(["git", "config", "user.name", "Dev"], cwd=repo_dir, check=True)
    (repo_dir / "app.ts").write_text("export const x = 1\n")
    sp.run(["git", "add", "."], cwd=repo_dir, check=True)
    sp.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "app.ts").write_text("export const x = 2\n")

    manager = get_session_manager()
    session = manager.create_session(
        "test-channel",
        "test-project",
        "Review the architecture",
        metadata={
            "supervised": True,
            "followup_instructions": [{
                "instruction": "Apply the recommendations and leave the files for review.",
                "created_at": "2026-11-04T15:00:00+00:00",
            }],
        },
    )
    artifact_dir = repo_dir / ".rdc" / "sessions"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / f"{session.id}.html").write_text("<h1>Architecture review</h1>")

    monkeypatch.setattr("remote_dev_ctrl.server.app._session_project_path", lambda _project: repo_dir)
    token, _ = get_auth_manager().create_token("followup-diff", Role.ADMIN)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    session_resp = client.get(f"/sessions/{session.id}", headers=headers)
    assert session_resp.status_code == 200
    assert session_resp.json()["followup_instructions"][0]["instruction"].startswith("Apply the recommendations")

    artifact_resp = client.get(f"/sessions/{session.id}/artifact", headers=headers)
    assert artifact_resp.status_code == 200
    assert artifact_resp.json()["source"] == "custom"
    assert "Architecture review" in artifact_resp.json()["html"]

    diff_resp = client.get(f"/sessions/{session.id}/diff", headers=headers)
    assert diff_resp.status_code == 200
    files = diff_resp.json()["files"]
    assert any(item["path"] == "app.ts" and "export const x = 2" in item["patch"] for item in files)

    # The host commit lands after the run. The follow-up view still has to show
    # those files, while the original review stays the custom artifact.
    parent = sp.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True,
    ).stdout.strip()
    sp.run(["git", "add", "app.ts"], cwd=repo_dir, check=True, capture_output=True)
    sp.run(["git", "commit", "-m", "apply"], cwd=repo_dir, check=True, capture_output=True)
    session.metadata = {
        **(session.metadata or {}),
        "git_baseline": {"head": parent, "status": ""},
    }
    manager._persist(session)

    committed = client.get(f"/sessions/{session.id}/diff?run=1", headers=headers)
    assert committed.status_code == 200
    committed_files = committed.json()["files"]
    assert any(item["path"] == "app.ts" and "export const x = 2" in item["patch"] for item in committed_files)
    review = client.get(f"/sessions/{session.id}/diff?run=0", headers=headers)
    assert review.status_code == 200
    assert review.json()["files"] == []


def test_session_git_status_and_one_click_host_commit(monkeypatch, tmp_path):
    import subprocess as sp

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    sp.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
    sp.run(["git", "config", "user.name", "Dev"], cwd=repo_dir, check=True)
    (repo_dir / "app.ts").write_text("export const x = 1\n")
    sp.run(["git", "add", "."], cwd=repo_dir, check=True)
    sp.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)

    # Make uncommitted edit
    (repo_dir / "app.ts").write_text("export const x = 2\n")

    manager = get_session_manager()
    session = manager.create_session(
        "test-channel",
        "test-project",
        "Look for any bugs and performance issues in the codebase, and fix them",
        metadata={"supervised": True},
    )
    session.status = SessionStatus.VERIFYING
    session.output_summary = (
        "`npm run verify` passes: 141 tests.\n\n"
        "Commit and push are blocked: this session cannot write to `.git`, and no remote is configured."
    )
    manager._persist(session)

    monkeypatch.setattr("remote_dev_ctrl.server.app._session_project_path", lambda _project: repo_dir)
    token, _ = get_auth_manager().create_token("git-host-commit-test", Role.ADMIN)
    client = TestClient(app)

    status_resp = client.get(
        f"/sessions/{session.id}/git-status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["dirty"] is True
    assert status_data["has_remote"] is False
    assert "app.ts" in status_data["changed_files"]
    assert "Commit and push are blocked" in (status_data["detected_blocker"] or "")
    assert any(s["kind"] == "host_commit" for s in status_data["suggested_next_steps"])

    # Verify proof artifact renders inline per-file diff before commit
    artifact_before = client.get(
        f"/sessions/{session.id}/artifact",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert artifact_before.status_code == 200
    assert "Code Changes (Per-File Diff)" in artifact_before.text
    assert "export const x = 2" in artifact_before.text

    commit_resp = client.post(
        f"/sessions/{session.id}/git-commit",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "fix: resolve bugs and performance issues\nCo-Authored-By: Someone <a@b.c>"},
    )
    assert commit_resp.status_code == 200
    commit_data = commit_resp.json()
    assert commit_data["success"] is True
    assert "Co-Authored-By" not in commit_data["message"]

    # Verify git evidence was auto-recorded, blocker was cleared, and diff persists in artifact after commit
    ev_resp = client.get(
        f"/sessions/{session.id}/evidence",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert any(item["evidence_type"] == "git" for item in ev_resp.json())
    updated_session = manager.get(session.id)
    assert "Commit and push are blocked" not in (updated_session.output_summary or "")
    assert "Host commit" in (updated_session.output_summary or "")

    artifact_after = client.get(
        f"/sessions/{session.id}/artifact",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert artifact_after.status_code == 200
    assert "Code Changes (Per-File Diff)" in artifact_after.text
    assert "export const x = 2" in artifact_after.text


def test_hyphenated_function_alias_invocation_and_shell_execution():
    from remote_dev_ctrl.server.agent_registry import wrap_alias_invocation
    import subprocess

    alias_info = {
        "id": "codex-personal",
        "family": "codex",
        "expansion": "codex-personal() {\n  echo \"ran func with args: $@\"\n}",
        "kind": "function",
    }
    wrapped = wrap_alias_invocation(alias_info, "codex", "--sandbox workspace-write")
    assert "codex_personal() {" in wrapped
    assert "codex_personal --sandbox workspace-write" in wrapped
    assert "codex-personal() {" not in wrapped

    # Verify that standard POSIX /bin/sh (which rejects hyphens in function declarations) parses and runs this cleanly
    res = subprocess.run(
        wrapped,
        shell=True,
        executable="/bin/sh",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "ran func with args: --sandbox workspace-write" in res.stdout
    assert "not a valid identifier" not in res.stderr


def test_start_supervised_session_does_not_switch_to_ready_family_alias(monkeypatch):
    import uuid
    from remote_dev_ctrl.server.db.models import Project
    from remote_dev_ctrl.server.db.repositories import get_project_repo
    from remote_dev_ctrl.server.supervised_sessions import (
        create_supervised_session,
        start_supervised_session,
    )

    sm = get_session_manager()
    db = sm._db()
    cid = f"ws-alias-test-{uuid.uuid4().hex[:6]}"
    db.execute(
        "INSERT OR IGNORE INTO channels (id, name, type) VALUES (?, ?, ?)",
        (cid, "No Alias Switch", "project"),
    )
    db.commit()

    pname = f"proj-{uuid.uuid4().hex[:6]}"
    get_project_repo().create(Project(name=pname, path=f"/tmp/{pname}"))

    session = create_supervised_session(
        channel_id=cid,
        project=pname,
        goal="Do not switch to alias",
        provider="codex",
    )
    assert session.agent_provider == "codex"

    # Mock discover_agent_aliases to provide a ready alias in the codex family
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_manager.discover_agent_aliases",
        lambda: {"codex-quill": {"family": "codex", "kind": "alias"}},
    )

    # Block 'codex', but allow 'codex-quill'
    def mock_blocker(provider: str):
        if provider == "codex":
            return "codex CLI not found in PATH"
        return None

    monkeypatch.setattr(
        "remote_dev_ctrl.server.supervised_sessions.agent_provider_blocker",
        mock_blocker,
    )

    # Starting session must fail with 'codex CLI not found in PATH' and NOT switch to 'codex-quill'
    with pytest.raises(RuntimeError, match="codex CLI not found in PATH"):
        start_supervised_session(session.id)

    fresh = sm.get(session.id)
    assert fresh is not None
    assert fresh.agent_provider == "codex"
    assert fresh.status == SessionStatus.FAILED
    assert "codex CLI not found in PATH" in (fresh.output_summary or "")





