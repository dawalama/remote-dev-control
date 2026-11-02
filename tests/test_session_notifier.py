"""SessionNotifier scrubbing, dedupe, and channel post behavior."""

from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager
from remote_dev_ctrl.server.session_notifier import (
    notify_session_event,
    notify_status_transition,
    reset_dedupe_for_tests,
    scrub_notification,
)


def test_scrub_redacts_secrets():
    goal_secret = "sk-" + ("a" * 24)
    reason_secret = "ghp_" + ("b" * 36)
    text = scrub_notification(
        session_id="sess-abc",
        goal=f"use {goal_secret}",
        status="needs_you",
        reason=f"token={reason_secret}",
    )
    assert goal_secret not in text
    assert reason_secret not in text
    assert "sk-" not in text
    assert "ghp_" not in text
    assert "[REDACTED]" in text
    assert "\n" not in text


def test_scrub_notification_truncates_and_omits_raw_logs():
    text = scrub_notification(
        session_id="sess-abc",
        goal="x" * 400,
        status="needs_you",
        reason="y" * 500,
    )
    assert "sess-abc" in text
    assert "needs_you" in text
    assert len(text) <= 600
    assert "\n" not in text


def test_notify_dedupes_same_event():
    reset_dedupe_for_tests()
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="notify-ws",
        project="notify-project",
        description="Notify me",
        metadata={"supervised": True},
    )
    first = notify_session_event(session, event="needs_you", reason="Approve please")
    second = notify_session_event(session, event="needs_you", reason="Approve please again")
    assert first.get("skipped") is None or first.get("channel") or first.get("skipped") in {
        "telegram_no_recipients",
        "no_event_loop",
    }
    assert second.get("skipped") == "deduped"


def test_request_approval_stores_scrubbed_summary():
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="notify-ws-secret",
        project="notify-project",
        description="Approval scrub",
        metadata={"supervised": True},
    )
    secret = "sk-" + ("c" * 24)
    approval_id = manager.request_approval(session, f"paste {secret} to continue", "term-1")
    row = manager._db().execute(
        "SELECT summary FROM session_approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    assert secret not in row["summary"]
    assert "[REDACTED]" in row["summary"]


def test_status_transition_only_notifies_attention_states():
    reset_dedupe_for_tests()
    manager = get_session_manager()
    session = manager.create_session(
        channel_id="notify-ws-2",
        project="notify-project",
        description="Running work",
        metadata={"supervised": True},
    )
    assert notify_status_transition(session, SessionStatus.RUNNING) is None
    result = notify_status_transition(session, SessionStatus.FAILED, reason="boom")
    assert result is not None
    assert result["event"] == "failed"
