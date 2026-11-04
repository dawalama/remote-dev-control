"""Unit tests for HomeBase BriefAssembler (deterministic, no LLM)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.brief import (
    BriefInput,
    PendingApproval,
    WaitingTerminal,
    assemble_brief,
    build_homebase_brief,
    user_facing_status,
)
from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager


@dataclass
class FakeSession:
    id: str
    channel_id: str = "ws-main"
    project: str = "demo"
    status: SessionStatus = SessionStatus.RUNNING
    goal: str = ""
    description: str = ""
    current_step: str | None = None
    blocker: str | None = None
    terminal_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


def test_user_facing_status_labels():
    assert user_facing_status(SessionStatus.PENDING) == "queued"
    assert user_facing_status(SessionStatus.RUNNING) == "working"
    assert user_facing_status(SessionStatus.WAITING) == "needs_you"
    assert user_facing_status(SessionStatus.VERIFYING) == "ready_for_review"
    assert user_facing_status(SessionStatus.COMPLETED) == "completed"


def test_brief_empty_homebase_recommends_start():
    brief = assemble_brief(
        BriefInput(default_workstream_hint="general")
    )
    assert brief["homebase"]["label"] == "HomeBase"
    assert brief["scope"] == "homebase"
    assert brief["active_sessions"] == []
    assert brief["needs_you"] == []
    assert brief["recommended_next"]["action"] == "start"
    assert "Nothing running" in brief["spoken_summary"]
    assert "general" in brief["spoken_summary"]


def test_brief_running_session_recommends_monitor():
    now = datetime.now()
    sessions = [
        FakeSession(
            id="sess-run",
            goal="Fix flaky auth tests",
            status=SessionStatus.RUNNING,
            created_at=now,
            current_step="Running pytest",
        )
    ]
    brief = assemble_brief(BriefInput(sessions=sessions))
    assert len(brief["active_sessions"]) == 1
    assert brief["active_sessions"][0]["status"] == "working"
    assert brief["active_sessions"][0]["goal"] == "Fix flaky auth tests"
    assert brief["recommended_next"]["action"] == "monitor"
    assert brief["recommended_next"]["session_id"] == "sess-run"
    assert "1 active session" in brief["spoken_summary"]
    assert "Nothing needs you" in brief["spoken_summary"]


def test_brief_waiting_session_recommends_check():
    now = datetime.now()
    sessions = [
        FakeSession(
            id="sess-wait",
            goal="Apply migration",
            status=SessionStatus.WAITING,
            blocker="Waiting for DB approval",
            created_at=now,
        ),
        FakeSession(
            id="sess-run",
            goal="Refactor helpers",
            status=SessionStatus.RUNNING,
            created_at=now - timedelta(hours=1),
        ),
    ]
    brief = assemble_brief(BriefInput(sessions=sessions))
    assert brief["recommended_next"]["action"] == "check"
    assert brief["recommended_next"]["session_id"] == "sess-wait"
    assert any(n["kind"] == "session_blocker" for n in brief["needs_you"])


def test_brief_pending_approval_outranks_waiting_and_running():
    now = datetime.now()
    sessions = [
        FakeSession(
            id="sess-old-wait",
            goal="Old waiting work",
            status=SessionStatus.WAITING,
            created_at=now - timedelta(hours=2),
        ),
        FakeSession(
            id="sess-run",
            goal="Running work",
            status=SessionStatus.RUNNING,
            created_at=now - timedelta(hours=1),
        ),
        FakeSession(
            id="sess-approve",
            goal="Deploy change",
            status=SessionStatus.RUNNING,
            created_at=now,
        ),
    ]
    approvals = [
        PendingApproval(
            id="approval-1",
            session_id="sess-approve",
            summary="Proceed with production deploy?",
            requested_at=(now - timedelta(minutes=5)).isoformat(),
        )
    ]
    brief = assemble_brief(BriefInput(sessions=sessions, pending_approvals=approvals))
    assert brief["recommended_next"]["action"] == "approve"
    assert brief["recommended_next"]["session_id"] == "sess-approve"
    assert brief["recommended_next"]["approval_id"] == "approval-1"
    assert any(n["kind"] == "approval" for n in brief["needs_you"])
    assert "Approve or reject" in brief["recommended_next"]["summary"]


def test_brief_oldest_approval_wins():
    now = datetime.now()
    sessions = [
        FakeSession(id="sess-a", goal="A", status=SessionStatus.RUNNING, created_at=now),
        FakeSession(id="sess-b", goal="B", status=SessionStatus.RUNNING, created_at=now),
    ]
    approvals = [
        PendingApproval(
            id="approval-new",
            session_id="sess-b",
            summary="New prompt",
            requested_at=now.isoformat(),
        ),
        PendingApproval(
            id="approval-old",
            session_id="sess-a",
            summary="Older prompt",
            requested_at=(now - timedelta(minutes=10)).isoformat(),
        ),
    ]
    brief = assemble_brief(BriefInput(sessions=sessions, pending_approvals=approvals))
    assert brief["recommended_next"]["approval_id"] == "approval-old"


def test_brief_ignores_approval_for_session_not_in_snapshot():
    now = datetime.now()
    brief = assemble_brief(
        BriefInput(
            sessions=[
                FakeSession(
                    id="sess-run",
                    goal="Running work",
                    status=SessionStatus.RUNNING,
                    created_at=now,
                )
            ],
            pending_approvals=[
                PendingApproval(
                    id="approval-gone",
                    session_id="sess-cancelled",
                    summary="Approve the cancelled session",
                    requested_at=now.isoformat(),
                )
            ],
        )
    )
    assert brief["needs_you"] == []
    assert brief["recommended_next"]["action"] == "monitor"
    assert brief["recommended_next"]["session_id"] == "sess-run"


def test_brief_one_wait_is_one_needs_you():
    now = datetime.now()
    brief = assemble_brief(
        BriefInput(
            sessions=[
                FakeSession(
                    id="sess-wait",
                    goal="Need input",
                    status=SessionStatus.WAITING,
                    blocker="Agent is waiting for input",
                    created_at=now,
                )
            ],
            pending_approvals=[
                PendingApproval(
                    id="approval-1",
                    session_id="sess-wait",
                    summary="Agent is waiting for input",
                    requested_at=now.isoformat(),
                )
            ],
            waiting_terminals=[
                WaitingTerminal(
                    terminal_id="term-1",
                    project="demo",
                    session_id="sess-wait",
                    summary="raw terminal tail should not be a second item",
                )
            ],
        )
    )
    assert len(brief["needs_you"]) == 1
    assert brief["needs_you"][0]["kind"] == "approval"
    assert brief["needs_you"][0]["session_id"] == "sess-wait"


def test_running_blocker_does_not_need_you():
    now = datetime.now()
    brief = assemble_brief(
        BriefInput(
            sessions=[
                FakeSession(
                    id="sess-run",
                    goal="Still working",
                    status=SessionStatus.RUNNING,
                    blocker="stale blocker from an earlier wait",
                    created_at=now,
                )
            ]
        )
    )
    assert brief["needs_you"] == []
    assert brief["recommended_next"]["action"] == "monitor"
    assert brief["recommended_next"]["session_id"] == "sess-run"


def test_pinned_brief_skips_unlinked_waiting_terminal():
    brief = assemble_brief(
        BriefInput(
            workstream_id="ws-pin",
            sessions=[
                FakeSession(
                    id="sess-pin",
                    channel_id="ws-pin",
                    goal="Pinned work",
                    status=SessionStatus.RUNNING,
                )
            ],
            waiting_terminals=[
                WaitingTerminal(
                    terminal_id="term-other",
                    project="other",
                    session_id=None,
                    summary="Shell waiting at prompt",
                )
            ],
        )
    )
    assert brief["needs_you"] == []
    assert brief["recommended_next"]["action"] == "monitor"
    assert brief["recommended_next"]["session_id"] == "sess-pin"


def test_resumed_session_drops_terminal_approval_from_brief():
    manager = get_session_manager()
    channel_id = f"brief-resume-{uuid.uuid4().hex[:8]}"
    session = manager.create_session(
        channel_id=channel_id,
        project="brief-project",
        description="Resume after a prompt",
        metadata={"supervised": True},
    )
    manager.update_status(session.id, SessionStatus.WAITING)
    approval_id = manager.request_approval(session, "Agent is waiting for input", "term-1")
    manager.update_status(session.id, SessionStatus.RUNNING)

    row = manager._db().execute(
        "SELECT status FROM session_approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    assert row["status"] == "resolved"

    brief = build_homebase_brief(workstream_id=channel_id)
    assert brief["needs_you"] == []
    assert brief["recommended_next"]["action"] == "monitor"
    assert brief["recommended_next"]["session_id"] == session.id


def test_brief_terminal_only_waiting():
    brief = assemble_brief(
        BriefInput(
            waiting_terminals=[
                WaitingTerminal(
                    terminal_id="term-1",
                    project="demo",
                    session_id=None,
                    summary="Shell waiting at prompt",
                )
            ]
        )
    )
    assert any(n["kind"] == "waiting_terminal" for n in brief["needs_you"])
    # No sessions/approvals → still surface needs_you; recommend start if nothing session-like
    assert brief["recommended_next"]["action"] == "start"
    assert "need" in brief["spoken_summary"].lower()


def test_brief_workstream_filter():
    now = datetime.now()
    sessions = [
        FakeSession(id="s1", channel_id="ws-a", goal="A work", created_at=now),
        FakeSession(id="s2", channel_id="ws-b", goal="B work", created_at=now),
    ]
    brief = assemble_brief(BriefInput(sessions=sessions, workstream_id="ws-a"))
    assert brief["scope"] == "workstream"
    assert len(brief["active_sessions"]) == 1
    assert brief["active_sessions"][0]["id"] == "s1"


def test_brief_collection_filter_and_precedence():
    now = datetime.now()
    sessions = [
        FakeSession(id="s1", channel_id="ws-sugar", goal="Sugarwork goal", created_at=now),
        FakeSession(id="s2", channel_id="ws-quill", goal="Quill goal", created_at=now),
        FakeSession(id="s3", channel_id="ws-other", goal="Other goal", created_at=now),
    ]
    channel_colls = {
        "ws-sugar": "coll-sugar",
        "ws-quill": "coll-quill",
        "ws-other": "coll-other",
    }
    # 1. collection_id keeps sessions from other collections out
    brief_coll = assemble_brief(
        BriefInput(sessions=sessions, collection_id="coll-sugar", channel_collections=channel_colls)
    )
    assert brief_coll["scope"] == "collection"
    assert len(brief_coll["active_sessions"]) == 1
    assert brief_coll["active_sessions"][0]["id"] == "s1"

    # 2. workstream_id still wins when both are set
    brief_both = assemble_brief(
        BriefInput(
            sessions=sessions,
            workstream_id="ws-quill",
            collection_id="coll-sugar",
            channel_collections=channel_colls,
        )
    )
    assert brief_both["scope"] == "workstream"
    assert len(brief_both["active_sessions"]) == 1
    assert brief_both["active_sessions"][0]["id"] == "s2"

    # 3. No arguments still returns every session
    brief_none = assemble_brief(
        BriefInput(sessions=sessions, channel_collections=channel_colls)
    )
    assert brief_none["scope"] == "homebase"
    assert len(brief_none["active_sessions"]) == 3


def test_brief_global_mentions_multiple_projects():
    now = datetime.now()
    sessions = [
        FakeSession(id="s1", project="alpha", goal="A", created_at=now),
        FakeSession(id="s2", project="beta", goal="B", created_at=now),
    ]
    brief = assemble_brief(BriefInput(sessions=sessions))
    assert brief["scope"] == "homebase"
    assert "Across projects" in brief["spoken_summary"]
    assert "alpha" in brief["spoken_summary"]
    assert "beta" in brief["spoken_summary"]


def test_goal_truncation():
    long_goal = "x" * 400
    brief = assemble_brief(
        BriefInput(
            sessions=[
                FakeSession(id="s1", goal=long_goal, status=SessionStatus.RUNNING)
            ]
        )
    )
    assert len(brief["active_sessions"][0]["goal"]) <= 160


def test_build_homebase_brief_from_session_manager():
    manager = get_session_manager()
    channel_id = f"brief-ws-{uuid.uuid4().hex[:8]}"
    session = manager.create_session(
        channel_id=channel_id,
        project="brief-project",
        description="Live brief integration",
        metadata={"supervised": True},
    )
    manager.update_status(session.id, SessionStatus.RUNNING)
    approval_id = manager.request_approval(session, "Continue with write?", "term-x")

    # Scope to this workstream so other live sessions on the machine do not win.
    brief = build_homebase_brief(workstream_id=channel_id)
    ids = {s["id"] for s in brief["active_sessions"]}
    assert session.id in ids
    assert brief["recommended_next"]["action"] == "approve"
    assert brief["recommended_next"]["approval_id"] == approval_id
    assert "session" in brief["spoken_summary"].lower()


def test_homebase_brief_endpoint_requires_auth_and_returns_structure():
    token, _ = get_auth_manager().create_token("brief-api-test", Role.ADMIN)
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)

    unauth = client.get("/homebase/brief")
    assert unauth.status_code in {401, 403}

    response = client.get("/homebase/brief", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["homebase"]["label"] == "HomeBase"
    assert "spoken_summary" in body
    assert "needs_you" in body
    assert "active_sessions" in body
    assert "recommended_next" in body
    assert body["recommended_next"]["action"] in {"approve", "check", "monitor", "start"}
