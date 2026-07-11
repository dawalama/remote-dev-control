"""Regression tests for extending the current session aggregate."""

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
    assert child.metadata["inherited_context"]["parent_result"] == "Build a dashboard endpoint and summary cards."
    repeated = TestClient(app).post(
        f"/sessions/{parent.id}/continue",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Accidentally create a sibling"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == child.id


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
    assert "gemini --prompt-interactive" in build_agent_command("gemini", "Review data")
    assert "diagnostic session" in build_agent_command("diagnostic", "No writes")
    codex_command = build_agent_command("codex", "Fix the homepage")
    assert "codex --ask-for-approval never exec --sandbox workspace-write" in codex_command


def test_cursor_provider_readiness_is_checked_before_terminal_creation(monkeypatch):
    class Result:
        returncode = 1

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
