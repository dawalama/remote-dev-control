"""Regression tests for extending the current session aggregate."""

from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.session_manager import (
    SessionManager,
    SessionStatus,
    build_agent_command,
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
        metadata={"supervised": True},
    )
    assert manager.successful_execution_status(session) == SessionStatus.VERIFYING


def test_supervised_completion_is_evidence_gated():
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Do not complete on an agent claim",
        metadata={"supervised": True},
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
    assert "cursor-agent status" in cursor_command
    assert "cursor-agent -p --trust" in cursor_command
    assert "claude" not in cursor_command
    assert "gemini --prompt-interactive" in build_agent_command("gemini", "Review data")
    assert "diagnostic session" in build_agent_command("diagnostic", "No writes")


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
