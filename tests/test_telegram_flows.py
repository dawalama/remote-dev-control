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
        if not repo.get("demo-project"):
            repo.create(Project(name="demo-project", path="/tmp"))

        with patch("remote_dev_ctrl.server.supervised_sessions.start_supervised_session") as mock_start:
            res = await handle_telegram_command("goal", "demo-project Fix test flakiness and lint", user_id=123)
            assert isinstance(res, dict)
            assert "Goal Session Launched" in res["text"]
            assert "demo-project" in res["text"]
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


def test_telegram_herd_agents_and_tasks_pocket_flow():
    """Verify Telegram pocket flow for Herd agents, /todo task cards, and pulling/sending."""
    async def _test():
        from remote_dev_ctrl.server.agent_herd import get_agent_herd_manager
        from remote_dev_ctrl.server.channel_manager import get_channel_manager
        from remote_dev_ctrl.server.db.repositories import get_project_repo

        proj = get_project_repo().get("demo-project")
        default_ch = get_channel_manager().get_project_default_channel(proj.id) if proj else None
        channel_id = default_ch.id if default_ch else "ch-system"

        herd = get_agent_herd_manager()
        worker = herd.spawn_worker(
            channel_id=channel_id,
            project="demo-project",
            provider="diagnostic",
        )

        # Queue a task card via Telegram add_task
        t_res = await handle_telegram_command("add_task", "demo-project|Verify pocket flow", user_id=123)
        assert isinstance(t_res, dict)
        assert "Task Card Queued" in t_res["text"]

        # View tasks
        board_res = await handle_telegram_command("herd_tasks", "", user_id=123)
        assert "Herd Task Board" in board_res["text"]
        assert "Verify pocket flow" in board_res["text"]

        # View agents
        agents_res = await handle_telegram_command("agents", "", user_id=123)
        assert "Resident Agents" in agents_res["text"]
        assert worker.name in agents_res["text"]

        # Peek clean agent output card (no raw terminal chrome)
        with patch.object(herd, "get_worker_clean_tail", return_value=["Running pytest...", "All 12 tests passed"]):
            peek_res = await handle_telegram_command("agent_peek", worker.id, user_id=123)
            assert "Latest Output" in peek_res["text"]
            assert "All 12 tests passed" in peek_res["text"]

        # Send prompt & pull task via mocked send_to_worker
        with patch.object(herd, "send_to_worker", return_value={"worker": worker.to_dict()}) as mock_send:
            send_res = await handle_telegram_command("agent_send_id", f"{worker.id}|check status", user_id=123)
            assert "Sent to" in send_res["text"]
            assert mock_send.called

            pull_res = await handle_telegram_command("agent_pull", worker.id, user_id=123)
            assert "Task Pulled" in pull_res["text"]

        # Unmatched plain text offers 3-button intent picker and queues task on draft_to_task
        draft_res = await handle_telegram_command("message", "Refactor auth cookie expiry", user_id=123)
        assert "What should I do with this?" in draft_res["text"]
        draft_actions = [b[1] for row in draft_res["buttons"] for b in row]
        assert "draft_to_task" in draft_actions
        assert "draft_to_agent" in draft_actions
        assert "draft_to_goal" in draft_actions

        queued = await handle_telegram_command("draft_to_task", "", user_id=123)
        assert "Task Card Queued" in queued["text"]
        assert "Refactor auth cookie expiry" in queued["text"]

    asyncio.run(_test())
