"""Session controls shared by the HTTP routes and the orchestrator tools.

Tool results stay on the session row. Terminal output and approval text stay out.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .db.repositories import get_project_repo
from .session_manager import Session, SessionStatus, get_session_manager

logger = logging.getLogger(__name__)

_GIT_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_INSTRUCT_STATUSES = {"verifying", "completed", "done", "failed", "cancelled"}
_ROW_FIELDS = ("id", "project", "goal", "status", "blocker", "control_owner")


class SessionControlError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _status_value(session: Session) -> str:
    return session.status.value if isinstance(session.status, SessionStatus) else str(session.status)


def session_row(session: Session) -> dict:
    """Fields the chat may repeat. Nothing from the terminal or the approval body."""
    values = {
        "id": session.id,
        "project": session.project,
        "goal": session.goal or session.description,
        "status": _status_value(session),
        "blocker": session.blocker,
        "control_owner": session.control_owner,
    }
    return {key: values[key] for key in _ROW_FIELDS}


def _project_keys(project: str) -> set[str]:
    keys = {project}
    record = get_project_repo().get(project)
    if record is not None:
        if record.name:
            keys.add(record.name)
        if record.id:
            keys.add(record.id)
    return keys


def list_sessions(project: str | None = None) -> list[dict]:
    """Active sessions. Optional project filter. Row fields only."""
    keys = _project_keys(project) if project else None
    rows = []
    for session in get_session_manager().list_active():
        if keys is not None and session.project not in keys:
            continue
        rows.append(session_row(session))
    return rows


def _approval_pending(session_id: str) -> bool:
    row = get_session_manager()._db().execute(
        "SELECT 1 FROM session_approvals WHERE session_id = ? AND status = 'pending' LIMIT 1",
        (session_id,),
    ).fetchone()
    return row is not None


def session_status(session_id: str) -> dict:
    session = get_session_manager().get(session_id)
    if not session:
        raise SessionControlError(404, "Session not found")
    row = session_row(session)
    row["output_summary"] = session.output_summary
    row["approval_pending"] = _approval_pending(session.id)
    return row


def cancel_session(session_id: str) -> Session:
    """Permanently stop active execution while retaining its audit record."""
    from .terminal import get_terminal_manager

    manager = get_session_manager()
    session = manager.get(session_id)
    if not session:
        raise SessionControlError(404, "Session not found")
    if session.status in {SessionStatus.COMPLETED, SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.CANCELLED}:
        raise SessionControlError(409, "Session is already terminal")
    if session.status == SessionStatus.VERIFYING:
        raise SessionControlError(409, "Execution has finished. Complete or discard the review outcome instead.")
    manager.stop_monitor(session_id)
    manager.update_status(session_id, SessionStatus.CANCELLED)
    for terminal_id in session.terminal_ids:
        try:
            get_terminal_manager().destroy(terminal_id)
        except Exception:
            logger.warning("Failed to stop terminal while cancelling session %s", session_id)
    fresh = manager.get(session_id)
    if fresh is None:
        raise SessionControlError(404, "Session not found")
    return fresh


def _valid_git_sha(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if _GIT_SHA.fullmatch(text) else None


def _base_head(project: str) -> str | None:
    try:
        record = get_project_repo().get(project)
        project_path = Path(record.path).resolve() if record and record.path else None
        if project_path is None or not (project_path / ".git").exists():
            return None
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return _valid_git_sha(rev.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def instruct_session(session_id: str, instruction: str) -> dict:
    """Append a durable follow-up and resume work in the same RDC Session."""
    text = (instruction or "").strip()
    if not text:
        raise SessionControlError(422, "Tell the agent what to do next")
    sm = get_session_manager()
    session = sm.get(session_id)
    if not session:
        raise SessionControlError(404, "Session not found")
    if _status_value(session) not in _INSTRUCT_STATUSES:
        raise SessionControlError(409, "Take control to guide an agent that is still running")

    followups = list((session.metadata or {}).get("followup_instructions") or [])
    followup = {
        "instruction": text,
        "prior_result": session.output_summary or "No prior outcome was recorded.",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    base_head = _base_head(session.project)
    if base_head:
        followup["base_head"] = base_head
    followups.append(followup)
    prior_metadata = dict(session.metadata or {})
    session.metadata = {**(session.metadata or {}), "followup_instructions": followups, "review_mode": "implementation"}
    sm._persist(session)
    sm.log_event(session, "session.instruction_added", {"instruction": text, "context_mode": "reconstructed"})
    from .supervised_sessions import start_supervised_session

    try:
        result = start_supervised_session(
            session_id,
            provider=session.agent_provider,
            continue_review=True,
        )
    except Exception as exc:
        session.metadata = prior_metadata
        sm._persist(session)
        if isinstance(exc, LookupError):
            raise SessionControlError(404, str(exc)) from exc
        if isinstance(exc, ValueError):
            raise SessionControlError(400, str(exc)) from exc
        if isinstance(exc, RuntimeError):
            raise SessionControlError(409, str(exc)) from exc
        logger.warning("Could not continue session %s: %s", session_id, type(exc).__name__)
        raise SessionControlError(500, "Could not continue the session") from exc
    return {**result, "context_mode": "reconstructed", "instruction": text}
