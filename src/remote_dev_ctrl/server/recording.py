"""rrweb session recording manager.

Handles buffering rrweb events received via CDP binding callbacks,
flushing to chunked JSON files, and retrieval for playback.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import get_rdc_home
from .db.connection import get_db
from .db.models import Recording, RecordingStatus

logger = logging.getLogger(__name__)

RECORDINGS_DIR = get_rdc_home() / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

MAX_EVENTS_PER_CHUNK = 1000
MAX_CHUNK_BYTES = 2 * 1024 * 1024  # 2MB
MAX_RECORDING_MINUTES = 30


class RecordingManager:
    """Manages rrweb recording sessions."""

    def __init__(self):
        self._buffers: dict[str, list[dict]] = {}  # recording_id -> events
        self._buffer_bytes: dict[str, int] = {}  # rough byte tracking

    def start_recording(self, session_id: str, project_id: str = "") -> Recording:
        """Create a new recording for a browser session."""
        recording_id = f"rec-{secrets.token_hex(4)}"

        recording = Recording(
            id=recording_id,
            session_id=session_id,
            project_id=project_id or None,
            status=RecordingStatus.RECORDING,
            started_at=datetime.now(),
        )

        # Create storage directory
        rec_dir = RECORDINGS_DIR / recording_id
        rec_dir.mkdir(parents=True, exist_ok=True)

        # Initialize buffer
        self._buffers[recording_id] = []
        self._buffer_bytes[recording_id] = 0

        # Save to DB
        self._save_recording(recording)
        logger.info(f"Started recording {recording_id} for session {session_id}")
        return recording

    def on_event(self, recording_id: str, event_json: str):
        """Buffer an incoming rrweb event."""
        if recording_id not in self._buffers:
            return

        try:
            event = json.loads(event_json)
        except json.JSONDecodeError:
            return

        self._buffers[recording_id].append(event)
        self._buffer_bytes[recording_id] = self._buffer_bytes.get(recording_id, 0) + len(event_json)

        # Flush if buffer is full
        if (len(self._buffers[recording_id]) >= MAX_EVENTS_PER_CHUNK or
                self._buffer_bytes[recording_id] >= MAX_CHUNK_BYTES):
            self._flush(recording_id)

    def stop_recording(self, recording_id: str) -> Optional[Recording]:
        """Stop a recording and flush remaining events."""
        recording = self._load_recording(recording_id)
        if not recording:
            return None

        # Flush remaining events
        self._flush(recording_id)

        # Clean up buffers
        self._buffers.pop(recording_id, None)
        self._buffer_bytes.pop(recording_id, None)

        # Update DB
        recording.status = RecordingStatus.STOPPED
        recording.stopped_at = datetime.now()
        self._save_recording(recording)

        logger.info(f"Stopped recording {recording_id}: {recording.event_count} events, {recording.chunk_count} chunks")
        return recording

    def get_recording(self, recording_id: str) -> Optional[Recording]:
        return self._load_recording(recording_id)

    def list_recordings(self, session_id: str = "", limit: int = 50) -> list[Recording]:
        db = get_db("rdc")
        if session_id:
            rows = db.execute(
                "SELECT * FROM recordings WHERE session_id = ? ORDER BY started_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM recordings ORDER BY started_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self._row_to_recording(r) for r in rows]

    def get_events(self, recording_id: str, chunk: int = 0) -> list[dict]:
        """Load events from a specific chunk file."""
        chunk_path = RECORDINGS_DIR / recording_id / f"chunk_{chunk}.json"
        if not chunk_path.exists():
            return []
        try:
            return json.loads(chunk_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def get_active_recording_for_session(self, session_id: str) -> Optional[Recording]:
        """Get the currently-active recording for a session, if any."""
        db = get_db("rdc")
        row = db.execute(
            "SELECT * FROM recordings WHERE session_id = ? AND status = 'recording' ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._row_to_recording(row) if row else None

    # -- Internals --

    def _flush(self, recording_id: str):
        """Write buffered events to a chunk file."""
        events = self._buffers.get(recording_id, [])
        if not events:
            return

        recording = self._load_recording(recording_id)
        if not recording:
            return

        chunk_num = recording.chunk_count
        chunk_path = RECORDINGS_DIR / recording_id / f"chunk_{chunk_num}.json"
        chunk_path.write_text(json.dumps(events))

        recording.event_count += len(events)
        recording.chunk_count = chunk_num + 1
        self._save_recording(recording)

        self._buffers[recording_id] = []
        self._buffer_bytes[recording_id] = 0

        logger.debug(f"Flushed {len(events)} events to {chunk_path}")

    def _save_recording(self, recording: Recording):
        db = get_db("rdc")
        db.execute("""
            INSERT OR REPLACE INTO recordings
            (id, session_id, project_id, status, started_at, stopped_at, event_count, chunk_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            recording.id, recording.session_id, recording.project_id,
            recording.status.value,
            recording.started_at.isoformat(),
            recording.stopped_at.isoformat() if recording.stopped_at else None,
            recording.event_count, recording.chunk_count,
        ))
        db.commit()

    def _load_recording(self, recording_id: str) -> Optional[Recording]:
        db = get_db("rdc")
        row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        return self._row_to_recording(row) if row else None

    def _row_to_recording(self, row) -> Recording:
        d = dict(row)
        return Recording(
            id=d["id"],
            session_id=d["session_id"],
            project_id=d.get("project_id"),
            status=RecordingStatus(d["status"]),
            started_at=datetime.fromisoformat(d["started_at"]) if d["started_at"] else datetime.now(),
            stopped_at=datetime.fromisoformat(d["stopped_at"]) if d.get("stopped_at") else None,
            event_count=d.get("event_count", 0),
            chunk_count=d.get("chunk_count", 0),
        )


# Global singleton
_recording_manager: Optional[RecordingManager] = None


def get_recording_manager() -> RecordingManager:
    global _recording_manager
    if _recording_manager is None:
        _recording_manager = RecordingManager()
    return _recording_manager
