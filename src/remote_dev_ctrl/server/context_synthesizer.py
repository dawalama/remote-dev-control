"""Context synthesizer — combines conversation threads + event store into structured context.

Pure Python, no LLM. Produces data that the system prompt template formats for the LLM.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SynthesizedContext:
    """Structured context for the LLM system prompt."""
    turns: list[dict] = field(default_factory=list)
    thread_summary: str | None = None
    recent_events: list[dict] = field(default_factory=list)
    client_state: dict | None = None


class ContextSynthesizer:
    """Combines conversation threads and event store into compact LLM context."""

    def synthesize(
        self,
        project: str | None,
        thread_id: str,
        client_id: str | None = None,
    ) -> SynthesizedContext:
        from .conversation import get_conversation_manager
        from .event_store import get_event_store

        conv_mgr = get_conversation_manager()
        event_store = get_event_store()

        # Conversation turns
        turns = conv_mgr.get_recent_turns(thread_id, n=20)
        thread = conv_mgr.get_thread(thread_id)
        thread_summary = thread.get("summary") if thread else None

        # Recent events from DuckDB
        recent_events = []
        try:
            recent_events = event_store.query(
                project=project,
                limit=10,
                start_time=datetime.now() - timedelta(minutes=15),
            )
        except Exception:
            logger.debug("Failed to query events for context", exc_info=True)

        # Last known client action
        client_state = None
        if client_id:
            try:
                client_events = event_store.query(
                    event_type="client_action" if False else None,  # query doesn't filter by client_id directly
                    limit=20,
                    start_time=datetime.now() - timedelta(minutes=15),
                )
                # Filter by client_id manually since DuckDB query doesn't support it natively
                for ev in client_events:
                    if ev.get("client_id") == client_id:
                        client_state = ev
                        break
            except Exception:
                logger.debug("Failed to query client state", exc_info=True)

        return SynthesizedContext(
            turns=turns,
            thread_summary=thread_summary,
            recent_events=recent_events,
            client_state=client_state,
        )

    def format_for_prompt(self, ctx: SynthesizedContext) -> str:
        """Format synthesized context as a string for the system prompt."""
        parts: list[str] = []

        if ctx.client_state:
            data = ctx.client_state.get("data") or {}
            action = data.get("action", "unknown")
            ts = ctx.client_state.get("timestamp", "")
            ago = self._time_ago(ts)
            client_id = ctx.client_state.get("client_id", "unknown")
            parts.append(f"Client: {client_id}, last action: {action} ({ago})")

        if ctx.recent_events:
            event_lines = []
            for ev in ctx.recent_events[:5]:  # Top 5 most recent
                etype = ev.get("event_type", "?")
                ts = ev.get("timestamp", "")
                ago = self._time_ago(ts)
                proj = ev.get("project") or ""
                proj_str = f" [{proj}]" if proj else ""
                data = ev.get("data") or {}
                detail = data.get("action") or data.get("process_id") or ""
                detail_str = f": {detail}" if detail else ""
                event_lines.append(f"  {etype}{detail_str}{proj_str} ({ago})")
            if event_lines:
                parts.append("Recent events:")
                parts.extend(event_lines)

        return "\n".join(parts)

    @staticmethod
    def _time_ago(iso_timestamp: str) -> str:
        """Convert ISO timestamp to human-readable 'Xm ago' string."""
        if not iso_timestamp:
            return "?"
        try:
            ts = datetime.fromisoformat(iso_timestamp)
            delta = datetime.now() - ts
            minutes = int(delta.total_seconds() / 60)
            if minutes < 1:
                return "just now"
            if minutes < 60:
                return f"{minutes}m ago"
            hours = minutes // 60
            return f"{hours}h ago"
        except (ValueError, TypeError):
            return "?"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_synthesizer: ContextSynthesizer | None = None


def get_context_synthesizer() -> ContextSynthesizer:
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = ContextSynthesizer()
    return _synthesizer
