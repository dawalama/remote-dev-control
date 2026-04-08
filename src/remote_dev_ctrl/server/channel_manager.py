"""Channel management for RDC v2.

Channels are workspaces that contain messages, terminals, and missions.
Each project gets a default channel. Users can create additional channels
per project, cross-project channels, or ephemeral channels.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Optional

from .db.connection import get_db
from .db.models import (
    Channel,
    ChannelMessage,
    ChannelMessageRole,
    ChannelType,
    StructuredEvent,
)

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manages channels, messages, and the event store."""

    def __init__(self):
        self.db = get_db("rdc")

    # ── Channel CRUD ──────────────────────────────────────────────

    def create_channel(
        self,
        name: str,
        type: ChannelType = ChannelType.PROJECT,
        project_ids: list[str] | None = None,
        parent_channel_id: str | None = None,
        collection_id: str = "general",
    ) -> Channel:
        """Create a new channel."""
        channel_id = f"ch-{secrets.token_hex(6)}"
        now = datetime.now()

        self.db.execute(
            """INSERT INTO channels (id, name, type, parent_channel_id, collection_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (channel_id, name, type.value, parent_channel_id, collection_id, now.isoformat()),
        )

        # Link projects
        for pid in (project_ids or []):
            self.db.execute(
                "INSERT OR IGNORE INTO channel_projects (channel_id, project_id) VALUES (?, ?)",
                (channel_id, pid),
            )

        self.db.commit()
        logger.info(f"Channel created: {channel_id} ({name}, type={type.value})")

        return Channel(
            id=channel_id,
            name=name,
            type=type,
            parent_channel_id=parent_channel_id,
            collection_id=collection_id,
            project_ids=project_ids or [],
            created_at=now,
        )

    def get_channel(self, channel_id: str) -> Optional[Channel]:
        """Get a channel by ID."""
        row = self.db.execute(
            "SELECT * FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_channel(row)

    def get_channel_by_name(self, name: str) -> Optional[Channel]:
        """Get a channel by name (e.g. '#chilly-snacks')."""
        row = self.db.execute(
            "SELECT * FROM channels WHERE name = ? AND archived_at IS NULL", (name,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_channel(row)

    def get_project_default_channel(self, project_id: str) -> Optional[Channel]:
        """Get the default (project-type) channel for a project."""
        row = self.db.execute(
            """SELECT c.* FROM channels c
               JOIN channel_projects cp ON c.id = cp.channel_id
               WHERE cp.project_id = ? AND c.type = 'project' AND c.archived_at IS NULL
               ORDER BY c.created_at ASC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_channel(row)

    def list_channels(self, include_archived: bool = False) -> list[Channel]:
        """List all channels."""
        if include_archived:
            rows = self.db.execute(
                "SELECT * FROM channels ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM channels WHERE archived_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_channel(r) for r in rows]

    def list_channels_for_project(self, project_id: str) -> list[Channel]:
        """List all active channels for a project."""
        rows = self.db.execute(
            """SELECT c.* FROM channels c
               JOIN channel_projects cp ON c.id = cp.channel_id
               WHERE cp.project_id = ? AND c.archived_at IS NULL
               ORDER BY c.created_at ASC""",
            (project_id,),
        ).fetchall()
        return [self._row_to_channel(r) for r in rows]

    def archive_channel(self, channel_id: str) -> bool:
        """Archive a channel (keeps data, removes from sidebar)."""
        self.db.execute(
            "UPDATE channels SET archived_at = ? WHERE id = ?",
            (datetime.now().isoformat(), channel_id),
        )
        self.db.commit()
        return True

    def rename_channel(self, channel_id: str, name: str) -> bool:
        """Rename a channel."""
        self.db.execute(
            "UPDATE channels SET name = ? WHERE id = ?", (name, channel_id)
        )
        self.db.commit()
        return True

    def set_auto_mode(self, channel_id: str, enabled: bool) -> bool:
        """Toggle auto-mode for a channel."""
        self.db.execute(
            "UPDATE channels SET auto_mode = ? WHERE id = ?",
            (enabled, channel_id),
        )
        self.db.commit()
        return True

    def ensure_project_channel(self, project_id: str, project_name: str) -> Channel:
        """Get or create the default channel for a project."""
        existing = self.get_project_default_channel(project_id)
        if existing:
            return existing
        # Inherit collection from the project
        collection_id = "general"
        try:
            from .db.repositories import get_project_repo
            p = get_project_repo().get_by_id(project_id) or get_project_repo().get(project_name)
            if p and p.collection_id:
                collection_id = p.collection_id
        except Exception:
            pass
        return self.create_channel(
            name=f"#{project_name}",
            type=ChannelType.PROJECT,
            project_ids=[project_id],
            collection_id=collection_id,
        )

    # ── Messages ──────────────────────────────────────────────────

    def post_message(
        self,
        channel_id: str,
        role: "ChannelMessageRole | str",
        content: str,
        metadata: dict | None = None,
    ) -> ChannelMessage:
        """Post a message to a channel."""
        # Accept both enum and string
        if isinstance(role, str):
            role = ChannelMessageRole(role)
        msg_id = f"msg-{secrets.token_hex(6)}"
        now = datetime.now()

        self.db.execute(
            """INSERT INTO channel_messages (id, channel_id, role, content, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (msg_id, channel_id, role.value, content,
             json.dumps(metadata) if metadata else None, now.isoformat()),
        )
        self.db.commit()

        return ChannelMessage(
            id=msg_id,
            channel_id=channel_id,
            role=role,
            content=content,
            metadata=metadata,
            created_at=now,
        )

    def list_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[ChannelMessage]:
        """List messages in a channel, newest first."""
        if before:
            rows = self.db.execute(
                """SELECT * FROM channel_messages
                   WHERE channel_id = ? AND created_at < ?
                   ORDER BY created_at DESC LIMIT ?""",
                (channel_id, before, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT * FROM channel_messages
                   WHERE channel_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (channel_id, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in reversed(rows)]  # chronological order

    # ── Terminal ↔ Channel linking ────────────────────────────────

    def link_terminal(self, terminal_id: str, channel_id: str) -> None:
        """Link a terminal to a channel."""
        self.db.execute(
            "INSERT OR IGNORE INTO terminal_channels (terminal_id, channel_id) VALUES (?, ?)",
            (terminal_id, channel_id),
        )
        self.db.commit()

    def unlink_terminal(self, terminal_id: str, channel_id: str) -> None:
        """Unlink a terminal from a channel."""
        self.db.execute(
            "DELETE FROM terminal_channels WHERE terminal_id = ? AND channel_id = ?",
            (terminal_id, channel_id),
        )
        self.db.commit()

    def get_channel_terminals(self, channel_id: str) -> list[str]:
        """Get terminal IDs linked to a channel."""
        rows = self.db.execute(
            "SELECT terminal_id FROM terminal_channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        return [r["terminal_id"] for r in rows]

    def get_terminal_channels(self, terminal_id: str) -> list[str]:
        """Get channel IDs a terminal belongs to."""
        rows = self.db.execute(
            "SELECT channel_id FROM terminal_channels WHERE terminal_id = ?",
            (terminal_id,),
        ).fetchall()
        return [r["channel_id"] for r in rows]

    # ── Event Store ───────────────────────────────────────────────

    def emit_event(
        self,
        type: str,
        channel_id: str | None = None,
        project_id: str | None = None,
        mission_id: str | None = None,
        data: dict | None = None,
    ) -> str:
        """Emit a structured event to the event store."""
        event_id = f"evt-{secrets.token_hex(6)}"
        now = datetime.now()

        self.db.execute(
            """INSERT INTO events (id, timestamp, type, channel_id, project_id, mission_id, data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_id, now.isoformat(), type, channel_id, project_id, mission_id,
             json.dumps(data) if data else None),
        )
        self.db.commit()
        return event_id

    def search_events(
        self,
        query: str | None = None,
        type: str | None = None,
        channel_id: str | None = None,
        limit: int = 50,
    ) -> list[StructuredEvent]:
        """Search events. FTS query or filter by type/channel."""
        if query:
            rows = self.db.execute(
                """SELECT e.* FROM events e
                   JOIN events_fts fts ON e.rowid = fts.rowid
                   WHERE events_fts MATCH ?
                   ORDER BY e.timestamp DESC LIMIT ?""",
                (query, limit),
            ).fetchall()
        elif type and channel_id:
            rows = self.db.execute(
                "SELECT * FROM events WHERE type = ? AND channel_id = ? ORDER BY timestamp DESC LIMIT ?",
                (type, channel_id, limit),
            ).fetchall()
        elif type:
            rows = self.db.execute(
                "SELECT * FROM events WHERE type = ? ORDER BY timestamp DESC LIMIT ?",
                (type, limit),
            ).fetchall()
        elif channel_id:
            rows = self.db.execute(
                "SELECT * FROM events WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?",
                (channel_id, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()

        return [self._row_to_event(r) for r in rows]

    # ── Helpers ────────────────────────────────────────────────────

    def _row_to_channel(self, row) -> Channel:
        d = dict(row)
        # Fetch linked project IDs
        prows = self.db.execute(
            "SELECT project_id FROM channel_projects WHERE channel_id = ?",
            (d["id"],),
        ).fetchall()
        project_ids = [r["project_id"] for r in prows]

        return Channel(
            id=d["id"],
            name=d["name"],
            type=ChannelType(d["type"]),
            parent_channel_id=d.get("parent_channel_id"),
            collection_id=d.get("collection_id", "general"),
            project_ids=project_ids,
            auto_mode=bool(d.get("auto_mode", False)),
            token_spent=d.get("token_spent", 0),
            token_budget=d.get("token_budget"),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
            archived_at=datetime.fromisoformat(d["archived_at"]) if d.get("archived_at") else None,
        )

    def _row_to_message(self, row) -> ChannelMessage:
        d = dict(row)
        return ChannelMessage(
            id=d["id"],
            channel_id=d["channel_id"],
            role=ChannelMessageRole(d["role"]),
            content=d.get("content"),
            metadata=json.loads(d["metadata"]) if d.get("metadata") else None,
            synced=bool(d.get("synced", True)),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(),
        )

    def _row_to_event(self, row) -> StructuredEvent:
        d = dict(row)
        return StructuredEvent(
            id=d["id"],
            timestamp=datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else datetime.now(),
            type=d["type"],
            channel_id=d.get("channel_id"),
            project_id=d.get("project_id"),
            mission_id=d.get("mission_id"),
            data=json.loads(d["data"]) if d.get("data") else None,
        )


# Global singleton
_channel_manager: Optional[ChannelManager] = None


def get_channel_manager() -> ChannelManager:
    global _channel_manager
    if _channel_manager is None:
        _channel_manager = ChannelManager()
    return _channel_manager


def emit(
    type: str,
    channel_id: str | None = None,
    project_id: str | None = None,
    data: dict | None = None,
) -> None:
    """Convenience function to emit a structured event.

    Safe to call from anywhere — silently fails if DB isn't ready.
    """
    try:
        get_channel_manager().emit_event(
            type=type,
            channel_id=channel_id,
            project_id=project_id,
            data=data,
        )
    except Exception:
        pass
