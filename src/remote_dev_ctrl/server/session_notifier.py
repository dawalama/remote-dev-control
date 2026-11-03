"""Session lifecycle notifications for HomeBase (channel + optional Telegram).

Control-plane only — no LLM. Scrubs outbound text before it leaves the machine.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .scrubber import SecretScrubber

logger = logging.getLogger(__name__)

# Pattern scrub only. Do not load the vault into this path.
_scrubber = SecretScrubber()

GOAL_LIMIT = 160
REASON_LIMIT = 240
MESSAGE_LIMIT = 600
DEDUP_SECONDS = 45

# event_key → last fire time
_recent: dict[str, float] = {}

_NOTIFY_STATUSES = frozenset({
    "waiting",
    "verifying",
    "done",
    "completed",
    "failed",
    "cancelled",
})


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def scrub_notification(
    *,
    session_id: str,
    goal: str,
    status: str,
    reason: str = "",
) -> str:
    """Build outbound notification text. Status and goal only, secrets removed."""
    parts = [
        f"HomeBase session {session_id}",
        f"Status: {status}",
        f"Goal: {_truncate(goal, GOAL_LIMIT)}",
    ]
    if reason:
        parts.append(f"Detail: {_truncate(reason, REASON_LIMIT)}")
    return _scrubber.scrub(_truncate(" · ".join(parts), MESSAGE_LIMIT))


def _dedupe(key: str) -> bool:
    now = time.time()
    # Opportunistic cleanup
    stale = [k for k, t in _recent.items() if now - t > DEDUP_SECONDS * 4]
    for k in stale:
        _recent.pop(k, None)
    last = _recent.get(key)
    if last is not None and now - last < DEDUP_SECONDS:
        return False
    _recent[key] = now
    return True


def reset_dedupe_for_tests() -> None:
    _recent.clear()


def notify_session_event(
    session: Any,
    *,
    event: str,
    reason: str = "",
    extra_key: str = "",
) -> dict[str, Any]:
    """Post scrubbed session event to workstream channel; Telegram if configured.

    Returns a small decision dict for tests/debugging.
    Never raises into the session lifecycle.
    """
    result: dict[str, Any] = {
        "event": event,
        "channel": False,
        "telegram": False,
        "skipped": None,
    }
    try:
        session_id = getattr(session, "id", None) or "unknown"
        status_obj = getattr(session, "status", event)
        status = status_obj.value if hasattr(status_obj, "value") else str(status_obj)
        goal = getattr(session, "goal", None) or getattr(session, "description", "") or ""
        channel_id = getattr(session, "channel_id", None)

        dedupe_key = f"{session_id}:{event}:{extra_key}"
        if not _dedupe(dedupe_key):
            result["skipped"] = "deduped"
            return result

        text = scrub_notification(
            session_id=session_id,
            goal=goal,
            status=status if event == "status" else event,
            reason=reason,
        )

        if channel_id:
            try:
                from .channel_manager import get_channel_manager

                get_channel_manager().post_message(
                    channel_id,
                    role="system",
                    content=text,
                    metadata={
                        "type": "session_notify",
                        "session_id": session_id,
                        "event": event,
                        "status": status,
                    },
                )
                result["channel"] = True
            except Exception:
                logger.debug("Session channel notify failed", exc_info=True)

        # Telegram only when allowed recipients are configured
        try:
            from . import app as app_module

            bot = getattr(app_module, "telegram_bot", None)
            allowed = getattr(bot, "allowed_users", None) if bot else None
            if bot and allowed:
                import asyncio

                async def _broadcast():
                    if hasattr(bot, "broadcast_session_event"):
                        await bot.broadcast_session_event(text, session_id=session_id, event_type=event, status=status)
                    else:
                        await bot.broadcast(text)

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_broadcast())
                    result["telegram"] = True
                except RuntimeError:
                    # No running loop (e.g. pure unit tests)
                    result["skipped"] = result.get("skipped") or "no_event_loop"
            elif bot and not allowed:
                logger.warning(
                    "Telegram enabled but no allowed_users configured; "
                    "skipping session notification (channel still posted)"
                )
                result["skipped"] = "telegram_no_recipients"
        except Exception:
            logger.debug("Telegram session notify failed", exc_info=True)

    except Exception:
        logger.debug("notify_session_event failed", exc_info=True)
        result["skipped"] = "error"

    return result


def notify_status_transition(session: Any, new_status: str, reason: str = "") -> Optional[dict[str, Any]]:
    """Notify when a session enters a user-relevant terminal/attention status."""
    status = new_status.value if hasattr(new_status, "value") else str(new_status)
    if status not in _NOTIFY_STATUSES:
        return None
    label = {
        "waiting": "needs_you",
        "verifying": "ready_for_review",
        "done": "completed",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(status, status)
    return notify_session_event(session, event=label, reason=reason or "")
