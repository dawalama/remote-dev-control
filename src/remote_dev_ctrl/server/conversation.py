"""Server-side conversation threads — per-project persistent conversation history.

Stores conversation turns in SQLite (rdc.db) so conversation state persists
across devices and sessions. Each project gets one thread; a NULL-project
thread serves as the global/cross-project conversation.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from .db.connection import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_threads (
    id TEXT PRIMARY KEY,
    project TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES conversation_threads(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    channel TEXT,
    client_id TEXT,
    actions JSON,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_thread
    ON conversation_turns(thread_id, created_at);
"""


def init_conversation_schema() -> None:
    """Create conversation tables if they don't exist. Called from init_databases()."""
    db = get_db("rdc")
    for statement in _SCHEMA_SQL.strip().split(";"):
        statement = statement.strip()
        if statement:
            db.execute(statement)
    db.commit()


# ---------------------------------------------------------------------------
# ConversationManager
# ---------------------------------------------------------------------------

class ConversationManager:
    """Manages per-project conversation threads."""

    COMPACT_THRESHOLD = 50  # Compact when turn count exceeds this
    COMPACT_KEEP_RECENT = 20  # Keep this many recent turns after compaction

    def __init__(self):
        self._db = get_db("rdc")

    # --- Thread lifecycle ---

    def get_or_create_thread(self, project: str | None) -> str:
        """Get existing thread for project, or create one. Returns thread_id."""
        if project:
            row = self._db.execute(
                "SELECT id FROM conversation_threads WHERE project = ?",
                (project,),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT id FROM conversation_threads WHERE project IS NULL",
            ).fetchone()

        if row:
            return row["id"]

        import uuid
        thread_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        self._db.execute(
            "INSERT INTO conversation_threads (id, project, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (thread_id, project, now, now),
        )
        self._db.commit()
        return thread_id

    def get_thread(self, thread_id: str) -> dict | None:
        """Get thread metadata."""
        row = self._db.execute(
            "SELECT id, project, created_at, updated_at, summary FROM conversation_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # --- Turns ---

    def append_turn(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        channel: str | None = None,
        client_id: str | None = None,
        actions: list[dict] | None = None,
    ) -> int:
        """Append a turn to a thread. Returns the turn ID."""
        now = datetime.now().isoformat(timespec="seconds")
        actions_json = json.dumps(actions) if actions else None
        cursor = self._db.execute(
            """INSERT INTO conversation_turns
               (thread_id, role, content, channel, client_id, actions, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, role, content, channel, client_id, actions_json, now),
        )
        # Update thread timestamp
        self._db.execute(
            "UPDATE conversation_threads SET updated_at = ? WHERE id = ?",
            (now, thread_id),
        )
        self._db.commit()

        turn_id = cursor.lastrowid

        # Auto-compact if needed
        count = self._turn_count(thread_id)
        if count > self.COMPACT_THRESHOLD:
            self.compact_thread(thread_id)

        return turn_id

    def get_recent_turns(self, thread_id: str, n: int = 20) -> list[dict]:
        """Get the N most recent turns for a thread, oldest-first."""
        rows = self._db.execute(
            """SELECT id, role, content, channel, client_id, actions, created_at
               FROM conversation_turns
               WHERE thread_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (thread_id, n),
        ).fetchall()

        turns = []
        for row in reversed(rows):  # Reverse to get oldest-first
            turn = dict(row)
            if turn.get("actions"):
                try:
                    turn["actions"] = json.loads(turn["actions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            turns.append(turn)
        return turns

    def _turn_count(self, thread_id: str) -> int:
        """Count turns in a thread."""
        row = self._db.execute(
            "SELECT COUNT(*) FROM conversation_turns WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row[0] if row else 0

    # --- Compaction ---

    def compact_thread(self, thread_id: str, keep_recent: int | None = None) -> None:
        """Compact old turns into thread summary. Pure Python, no LLM.

        Concatenates old turns (beyond keep_recent) into a plain-text summary
        stored in the thread's `summary` field, then deletes those old turns.
        """
        keep = keep_recent or self.COMPACT_KEEP_RECENT

        # Get all turns ordered by time
        rows = self._db.execute(
            """SELECT id, role, content, created_at
               FROM conversation_turns
               WHERE thread_id = ?
               ORDER BY created_at ASC, id ASC""",
            (thread_id,),
        ).fetchall()

        if len(rows) <= keep:
            return

        old_turns = rows[:-keep]
        old_ids = [r["id"] for r in old_turns]

        # Build summary text from old turns
        summary_parts = []
        thread = self.get_thread(thread_id)
        if thread and thread.get("summary"):
            summary_parts.append(thread["summary"])

        for row in old_turns:
            ts = row["created_at"][:16]  # YYYY-MM-DDTHH:MM
            role = row["role"]
            content = row["content"][:200]  # Truncate long messages
            summary_parts.append(f"[{ts}] {role}: {content}")

        new_summary = "\n".join(summary_parts)
        # Cap summary at ~4000 chars to keep it manageable
        if len(new_summary) > 4000:
            new_summary = new_summary[-4000:]

        # Update thread summary and delete old turns
        now = datetime.now().isoformat(timespec="seconds")
        self._db.execute(
            "UPDATE conversation_threads SET summary = ?, updated_at = ? WHERE id = ?",
            (new_summary, now, thread_id),
        )
        placeholders = ",".join("?" for _ in old_ids)
        self._db.execute(
            f"DELETE FROM conversation_turns WHERE id IN ({placeholders})",
            old_ids,
        )
        self._db.commit()
        logger.info("Compacted thread %s: removed %d old turns", thread_id, len(old_ids))

    # --- Clear ---

    def clear_thread(self, project: str | None) -> bool:
        """Clear all turns for a project's thread. Returns True if thread existed."""
        if project:
            row = self._db.execute(
                "SELECT id FROM conversation_threads WHERE project = ?",
                (project,),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT id FROM conversation_threads WHERE project IS NULL",
            ).fetchone()

        if not row:
            return False

        thread_id = row["id"]
        self._db.execute("DELETE FROM conversation_turns WHERE thread_id = ?", (thread_id,))
        self._db.execute(
            "UPDATE conversation_threads SET summary = NULL, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), thread_id),
        )
        self._db.commit()
        return True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: ConversationManager | None = None


def get_conversation_manager() -> ConversationManager:
    """Get the global ConversationManager instance."""
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager
