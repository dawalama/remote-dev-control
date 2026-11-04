"""Shared supervised session create path."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.db.models import Project
from remote_dev_ctrl.server.db.repositories import get_project_repo
from remote_dev_ctrl.server.intent import (
    TOOLS_WITH_OUTPUT,
    ToolCall,
    _auto_confirm,
    _prefer_spawn_result,
)
from remote_dev_ctrl.server.session_manager import SessionManager, SessionStatus, get_session_manager
from remote_dev_ctrl.server.supervised_sessions import (
    _load_agent_provider_policy,
    create_supervised_session,
    start_supervised_session,
)


def _channel(channel_id: str, name: str = "test-ws", type: str = "project", collection_id: str | None = None) -> str:
    sm = get_session_manager()
    db = sm._db()
    db.execute(
        "INSERT OR IGNORE INTO channels (id, name, type, collection_id) VALUES (?, ?, ?, ?)",
        (channel_id, name, type, collection_id),
    )
    db.commit()
    return channel_id


def _project_obj(prefix: str) -> Project:
    name = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return get_project_repo().create(
        Project(name=name, path=f"/tmp/{name}", description="test")
    )


def _project(prefix: str) -> str:
    return _project_obj(prefix).name


def test_create_supervised_session_sets_metadata_and_goal():
    _channel("ws-sup")
    get_project_repo().create(
        Project(
            name="supervised-demo",
            path="/tmp/supervised-demo",
            description="test",
        )
    )
    session = create_supervised_session(
        channel_id="ws-sup",
        project="supervised-demo",
        goal="Ship the hike demo brief",
        provider="diagnostic",
        acceptance_criteria=["Tests pass"],
        policy={"allow_network": False},
    )
    assert session.goal == "Ship the hike demo brief"
    assert session.metadata is not None
    assert session.metadata.get("supervised") is True
    assert "git_baseline" in session.metadata
    assert "evidence_providers" in session.metadata
    assert session.acceptance_criteria == ["Tests pass"]
    assert session.policy == {"allow_network": False}
    assert session.agent_provider == "diagnostic"


def test_claim_for_start_allows_one_winner():
    project = _project("claim")
    _channel("ws-claim")
    session = create_supervised_session(
        channel_id="ws-claim",
        project=project,
        goal="One start only",
        provider="diagnostic",
    )
    manager = get_session_manager()
    assert manager.claim_for_start(session.id) == "pending"
    assert manager.claim_for_start(session.id) is None
    assert manager.get(session.id).status == SessionStatus.RUNNING


def test_channel_post_failure_leaves_session_running(monkeypatch):
    project = _project("postfail")
    _channel("ws-postfail")
    session = create_supervised_session(
        channel_id="ws-postfail",
        project=project,
        goal="Stay running when the channel post fails",
        provider="diagnostic",
    )

    class FakeTerms:
        def create(self, **_kwargs):
            return SimpleNamespace(id="term-slice1")

    class BoomChannel:
        def post_message(self, *_args, **_kwargs):
            raise RuntimeError("channel down")

        def emit_event(self, *_args, **_kwargs):
            raise RuntimeError("channel down")

    import remote_dev_ctrl.server.channel_manager as channel_mod
    import remote_dev_ctrl.server.terminal as terminal_mod

    monkeypatch.setattr(terminal_mod, "get_terminal_manager", lambda: FakeTerms())
    monkeypatch.setattr(channel_mod, "get_channel_manager", lambda: BoomChannel())
    monkeypatch.setattr(
        SessionManager,
        "_start_terminal_monitor",
        lambda self, session, terminal_id: None,
    )

    result = start_supervised_session(session.id)
    assert result["success"] is True
    assert result["terminal_id"] == "term-slice1"
    fresh = get_session_manager().get(session.id)
    assert fresh is not None
    assert fresh.status == SessionStatus.RUNNING


def test_claim_for_start_continues_review_only_when_asked():
    project = _project("review-claim")
    _channel("ws-review-claim")
    session = create_supervised_session(
        channel_id="ws-review-claim",
        project=project,
        goal="Keep the review until a follow-up asks to continue",
        provider="diagnostic",
    )
    manager = get_session_manager()
    manager.update_status(session.id, SessionStatus.COMPLETED, output_summary="Ready for review")
    assert manager.claim_for_start(session.id) is None
    assert manager.get(session.id).status == SessionStatus.COMPLETED
    assert manager.claim_for_start(session.id, continue_review=True) == "completed"
    assert manager.get(session.id).status == SessionStatus.RUNNING


def test_continue_review_restores_status_when_provider_is_not_ready(monkeypatch):
    project = _project("review-restore")
    _channel("ws-review-restore")
    session = create_supervised_session(
        channel_id="ws-review-restore",
        project=project,
        goal="Stay in review if the agent cannot start",
        provider="diagnostic",
    )
    manager = get_session_manager()
    manager.update_status(session.id, SessionStatus.VERIFYING, output_summary="Ready for review")
    monkeypatch.setattr(
        "remote_dev_ctrl.server.supervised_sessions.agent_provider_blocker",
        lambda _provider: "not ready",
    )
    with pytest.raises(RuntimeError, match="not ready"):
        start_supervised_session(session.id, continue_review=True)
    fresh = manager.get(session.id)
    assert fresh is not None
    assert fresh.status == SessionStatus.VERIFYING
    assert fresh.output_summary == "Ready for review"


def test_create_session_rejects_system_channel():
    _channel("ch-system", type="system")
    project = _project("sys-reject")
    with pytest.raises(ValueError, match="system channel"):
        create_supervised_session(
            channel_id="ch-system",
            project=project,
            goal="Disallowed on system",
        )

    token, _ = get_auth_manager().create_token("sys-test", Role.ADMIN)
    client = TestClient(app)
    resp = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel_id": "ch-system", "project": project, "goal": "Disallowed on system"},
    )
    assert resp.status_code == 422


def test_create_session_rejects_unlinked_project():
    cid = _channel(f"ws-linked-{uuid.uuid4().hex[:6]}")
    proj_linked = _project_obj("linked-p")
    proj_unlinked = _project("unlinked-p")
    sm = get_session_manager()
    db = sm._db()
    # Link proj_linked to cid
    db.execute("INSERT INTO channel_projects (channel_id, project_id) VALUES (?, ?)", (cid, proj_linked.id))
    db.commit()

    with pytest.raises(ValueError, match="not linked"):
        create_supervised_session(
            channel_id=cid,
            project=proj_unlinked,
            goal="Should fail because unlinked",
        )

    token, _ = get_auth_manager().create_token("unlink-test", Role.ADMIN)
    client = TestClient(app)
    resp = client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel_id": cid, "project": proj_unlinked, "goal": "Should fail because unlinked"},
    )
    assert resp.status_code == 422


def test_create_session_channel_with_no_links_allows_any_project():
    cid = _channel(f"ws-nolinks-{uuid.uuid4().hex[:6]}")
    project = _project("any-proj")
    session = create_supervised_session(
        channel_id=cid,
        project=project,
        goal="Works on channel with 0 links",
    )
    assert session.project == project


def test_create_session_uses_collection_default_agent():
    coll_id = f"coll-{uuid.uuid4().hex[:6]}"
    sm = get_session_manager()
    db = sm._db()
    db.execute(
        "INSERT INTO collections (id, name, default_agent_id) VALUES (?, ?, ?)",
        (coll_id, "Test Coll", "custom-agent-1"),
    )
    db.commit()

    cid = _channel(f"ws-coll-{uuid.uuid4().hex[:6]}", collection_id=coll_id)
    project = _project("coll-proj")
    session = create_supervised_session(
        channel_id=cid,
        project=project,
        goal="Uses collection default",
    )
    assert session.agent_provider == "custom-agent-1"


def test_create_session_null_default_stores_preferred_provider():
    coll_id = f"coll-{uuid.uuid4().hex[:6]}"
    sm = get_session_manager()
    db = sm._db()
    db.execute(
        "INSERT INTO collections (id, name, default_agent_id) VALUES (?, ?, NULL)",
        (coll_id, "Null Agent Coll"),
    )
    db.commit()

    cid = _channel(f"ws-null-{uuid.uuid4().hex[:6]}", collection_id=coll_id)
    project = _project("null-proj")
    session = create_supervised_session(
        channel_id=cid,
        project=project,
        goal="Uses preferred provider",
    )
    pref = _load_agent_provider_policy().get("preferred_provider", "codex")
    assert session.agent_provider == pref


def test_create_session_explicit_provider_stored_as_given():
    cid = _channel(f"ws-explicit-{uuid.uuid4().hex[:6]}")
    project = _project("explicit-proj")
    session = create_supervised_session(
        channel_id=cid,
        project=project,
        goal="Explicit provider",
        provider="custom-explicit-agent",
    )
    assert session.agent_provider == "custom-explicit-agent"


def test_auto_confirm_uses_spawn_result():
    text = _auto_confirm([
        ToolCall(
            name="spawn_agent",
            params={"project": "demo"},
            result={"success": False, "output": "Failed to start the session on demo: not ready"},
        )
    ])
    assert "Failed to start the session on demo: not ready" in text
    assert "Started supervised session" not in text


def test_spawn_reply_reports_failure_instead_of_done():
    assert "spawn_agent" in TOOLS_WITH_OUTPUT
    failed = ToolCall(
        name="spawn_agent",
        params={"project": "demo"},
        result={"success": False, "output": "Failed to start the session on demo: not ready"},
    )
    assert _prefer_spawn_result("Done.", [failed]) == "Failed to start the session on demo: not ready"
    started = ToolCall(
        name="spawn_agent",
        params={"project": "demo"},
        result={"success": True, "output": "Started session sess-1 on demo with codex."},
    )
    assert _prefer_spawn_result("Done", [started]) == "Started session sess-1 on demo with codex."
    assert _prefer_spawn_result(
        "Started session sess-1 on demo with codex.",
        [started],
    ) == "Started session sess-1 on demo with codex."
