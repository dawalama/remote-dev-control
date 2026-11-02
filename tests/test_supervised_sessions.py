"""Shared supervised session create path."""

import uuid
from types import SimpleNamespace

import pytest

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
    create_supervised_session,
    start_supervised_session,
)


def test_create_supervised_session_sets_metadata_and_goal():
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
        provider="orchestrated",
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
    assert session.agent_provider == "orchestrated"


def _project(prefix: str) -> str:
    name = f"{prefix}-{uuid.uuid4().hex[:8]}"
    get_project_repo().create(
        Project(name=name, path=f"/tmp/{name}", description="test")
    )
    return name


def test_claim_for_start_allows_one_winner():
    project = _project("claim")
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
