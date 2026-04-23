"""Shared voice session runtime.

This module is intentionally provider-neutral. Browser mic, phone calls,
LiveKit, or future realtime providers should all report through this same
runtime so the rest of RDC sees one voice state shape.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)


VoiceTransport = Literal["browser", "phone", "livekit", "unknown"]
VoiceState = Literal["connecting", "listening", "processing", "speaking", "idle", "ended", "error"]

# Ended sessions are retained briefly so the UI can show their final state,
# then purged to keep memory bounded.
_ENDED_RETENTION_SECONDS = 60


@dataclass
class VoiceSession:
    id: str
    transport: VoiceTransport
    state: VoiceState = "connecting"
    channel: str = "desktop"
    client_id: str | None = None
    project: str | None = None
    external_id: str | None = None
    turn_count: int = 0
    last_transcript: str | None = None
    last_response: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "transport": self.transport,
            "state": self.state,
            "channel": self.channel,
            "client_id": self.client_id,
            "project": self.project,
            "external_id": self.external_id,
            "turn_count": self.turn_count,
            "last_transcript": self.last_transcript,
            "last_response": self.last_response,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


class VoiceRuntime:
    """In-memory voice session registry for active interaction state."""

    def __init__(self):
        self._sessions: dict[str, VoiceSession] = {}
        self._external_index: dict[str, str] = {}
        self._broadcast_pending = False

    def begin_session(
        self,
        *,
        transport: VoiceTransport = "unknown",
        channel: str = "desktop",
        client_id: str | None = None,
        project: str | None = None,
        external_id: str | None = None,
        state: VoiceState = "connecting",
    ) -> VoiceSession:
        # If another active session already holds this external_id, end it first
        # so the index isn't orphaned.
        if external_id:
            existing_id = self._external_index.get(external_id)
            if existing_id and existing_id in self._sessions:
                prior = self._sessions[existing_id]
                if prior.state not in {"ended", "error"}:
                    prior.state = "ended"
                    prior.ended_at = datetime.now()
                    prior.updated_at = prior.ended_at

        self._gc_ended()
        session = VoiceSession(
            id=f"voice-{secrets.token_hex(6)}",
            transport=transport,
            channel=channel,
            client_id=client_id,
            project=project,
            external_id=external_id,
            state=state,
        )
        self._sessions[session.id] = session
        if external_id:
            self._external_index[external_id] = session.id
        self._schedule_broadcast()
        return session

    def get(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)

    def get_by_external_id(self, external_id: str | None) -> VoiceSession | None:
        if not external_id:
            return None
        session_id = self._external_index.get(external_id)
        return self._sessions.get(session_id) if session_id else None

    def update_session(
        self,
        session_id: str,
        *,
        state: VoiceState | None = None,
        project: str | None = None,
        transcript: str | None = None,
        response: str | None = None,
        error: str | None = None,
        increment_turn: bool = False,
    ) -> VoiceSession | None:
        session = self._sessions.get(session_id)
        if not session:
            return None
        if state:
            session.state = state
        if project is not None:
            session.project = project
        if transcript is not None:
            session.last_transcript = transcript
        if response is not None:
            session.last_response = response
        if error is not None:
            session.error = error
            session.state = "error"
        if increment_turn:
            session.turn_count += 1
        session.updated_at = datetime.now()
        self._schedule_broadcast()
        return session

    def end_session(self, session_id: str | None, *, error: str | None = None) -> VoiceSession | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if not session:
            return None
        session.state = "error" if error else "ended"
        session.error = error
        session.ended_at = datetime.now()
        session.updated_at = session.ended_at
        self._schedule_broadcast()
        return session

    def snapshot(self) -> dict:
        self._gc_ended()
        sessions = [s.to_dict() for s in self._sessions.values() if s.state != "ended"]
        return {
            "active": any(s["state"] not in {"ended", "error"} for s in sessions),
            "sessions": sessions,
        }

    def _gc_ended(self) -> None:
        """Purge sessions that ended more than _ENDED_RETENTION_SECONDS ago."""
        cutoff = datetime.now() - timedelta(seconds=_ENDED_RETENTION_SECONDS)
        stale_ids = [
            sid for sid, s in self._sessions.items()
            if s.ended_at and s.ended_at < cutoff
        ]
        for sid in stale_ids:
            session = self._sessions.pop(sid, None)
            if session and session.external_id:
                # Only drop the index entry if it still points at this session
                if self._external_index.get(session.external_id) == sid:
                    self._external_index.pop(session.external_id, None)

    def _schedule_broadcast(self) -> None:
        if self._broadcast_pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._broadcast_pending = True
        loop.create_task(self._run_broadcast())

    async def _run_broadcast(self) -> None:
        try:
            # Yield once so multiple updates in the same tick coalesce into one broadcast.
            await asyncio.sleep(0)
            await _broadcast_state()
        finally:
            self._broadcast_pending = False


async def _broadcast_state() -> None:
    try:
        from .state_machine import get_state_machine

        await get_state_machine()._broadcast_state()
    except Exception:
        logger.debug("Voice runtime broadcast failed", exc_info=True)


_voice_runtime: VoiceRuntime | None = None


def get_voice_runtime() -> VoiceRuntime:
    global _voice_runtime
    if _voice_runtime is None:
        _voice_runtime = VoiceRuntime()
    return _voice_runtime
