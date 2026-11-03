"""Unit tests for modernized goal- and review-based Telegram flows."""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from remote_dev_ctrl.server.session_manager import get_session_manager, SessionStatus
from remote_dev_ctrl.server.app import handle_telegram_command
from remote_dev_ctrl.server.channels.telegram import TelegramBot


def test_telegram_status_command():
    """Verify /status returns goal-centered overview with inline buttons."""
    async def _test():
        res = await handle_telegram_command("status", "", user_id=123)
        assert isinstance(res, dict)
        assert "Goal Sessions" in res["text"]
        assert "buttons" in res
        button_labels = [b[0] for row in res["buttons"] for b in row]
        assert "🎯 Sessions" in button_labels
        assert any("Reviews" in b for b in button_labels)
        assert "🧭 Brief Me" in button_labels

    asyncio.run(_test())


def test_telegram_brief_command():
    """Verify /brief returns HomeBase control-plane brief."""
    async def _test():
        res = await handle_telegram_command("brief", "", user_id=123)
        assert isinstance(res, dict)
        assert "HomeBase Control-Plane Brief" in res["text"]
        assert "buttons" in res
        button_labels = [b[0] for row in res["buttons"] for b in row]
        assert "🎯 Sessions" in button_labels

    asyncio.run(_test())


def test_telegram_sessions_command():
    """Verify /sessions lists recent goal sessions."""
    async def _test():
        res = await handle_telegram_command("sessions", "", user_id=123)
        assert isinstance(res, dict)
        assert "Goal Sessions" in res["text"]
        assert "buttons" in res

    asyncio.run(_test())


def test_telegram_reviews_and_accept_flow():
    """Verify /reviews, /proof, and /accept flow for reviewing sessions."""
    async def _test():
        sm = get_session_manager()
        # Create a mock session in verifying state
        session = sm.create_session(
            channel_id="ch_test",
            project="test_proj",
            description="Test goal for review",
            metadata={"supervised": True},
            acceptance_criteria=["Check 1 passes", "Check 2 passes"]
        )
        sm.update_status(session.id, SessionStatus.VERIFYING, output_summary="All 10 tests passed successfully.")

        # Test /reviews
        reviews_res = await handle_telegram_command("reviews", "", user_id=123)
        assert isinstance(reviews_res, dict)
        assert session.id[:8] in reviews_res["text"] or "test_proj" in reviews_res["text"]

        # Test /proof
        proof_res = await handle_telegram_command("proof_session", session.id, user_id=123)
        assert isinstance(proof_res, dict)
        assert "Proof Artifact" in proof_res["text"]
        assert "All 10 tests passed successfully." in proof_res["text"]

        # Test /accept
        accept_res = await handle_telegram_command("accept_session", session.id, user_id=123)
        assert isinstance(accept_res, dict)
        assert "Accepted & Completed" in accept_res["text"]

        # Verify session is now completed
        updated = sm.get(session.id)
        assert updated.status == SessionStatus.COMPLETED

    asyncio.run(_test())


def test_telegram_goal_creation():
    """Verify /goal creates and starts a supervised session."""
    async def _test():
        from remote_dev_ctrl.server.db.repositories import get_project_repo
        from remote_dev_ctrl.server.db.models import Project
        repo = get_project_repo()
        if not repo.get("tesla-light-show"):
            repo.create(Project(name="tesla-light-show", path="/tmp"))

        with patch("remote_dev_ctrl.server.supervised_sessions.start_supervised_session") as mock_start:
            res = await handle_telegram_command("goal", "tesla-light-show Fix test flakiness and lint", user_id=123)
            assert isinstance(res, dict)
            assert "Goal Session Launched" in res["text"]
            assert "tesla-light-show" in res["text"]
            assert mock_start.called

    asyncio.run(_test())


def test_telegram_nlp_routing():
    """Verify natural language queries route to goal and review actions."""
    async def _test():
        # Brief intent
        brief_res = await handle_telegram_command("message", "brief me please", user_id=123)
        assert isinstance(brief_res, dict)
        assert "HomeBase Control-Plane Brief" in brief_res["text"]

        # Reviews intent
        rev_res = await handle_telegram_command("message", "what needs review?", user_id=123)
        assert isinstance(rev_res, dict)
        assert "Sessions Awaiting Review" in rev_res["text"]

        # Sessions intent
        sess_res = await handle_telegram_command("message", "show active sessions", user_id=123)
        assert isinstance(sess_res, dict)
        assert "Goal Sessions" in sess_res["text"]

    asyncio.run(_test())


def test_telegram_bot_session_event_broadcast():
    """Verify broadcast_session_event attaches interactive action buttons."""
    async def _test():
        bot = TelegramBot(token="fake_token", allowed_users=[12345])
        mock_bot = AsyncMock()
        bot._bot = mock_bot

        # Broadcast verifying event
        await bot.broadcast_session_event(
            text="Session sess-123 is ready for review!",
            session_id="sess-123",
            event_type="status",
            status="verifying"
        )
        assert mock_bot.send_message.called
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs["chat_id"] == 12345
        assert call_kwargs["reply_markup"] is not None
        # Check inline buttons include Accept and Proof
        keyboard = call_kwargs["reply_markup"].inline_keyboard
        button_texts = [btn.text for row in keyboard for btn in row]
        assert "✅ Accept & Complete" in button_texts
        assert "📋 View Proof" in button_texts

    asyncio.run(_test())
