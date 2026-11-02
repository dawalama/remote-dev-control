"""HomeBase brief assembler — deterministic control-plane status.

No LLM. Pure composition of sessions, approvals, and waiting terminals
for voice/text "brief me" and later phone TTS reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from .session_manager import Session, SessionStatus

GOAL_LIMIT = 160
REASON_LIMIT = 240
SPOKEN_LIMIT = 1200

# Product-facing status labels (see sessions hub mapping).
_STATUS_LABELS: dict[str, str] = {
    SessionStatus.PENDING.value: "queued",
    SessionStatus.RUNNING.value: "working",
    SessionStatus.WAITING.value: "needs_you",
    SessionStatus.VERIFYING.value: "ready_for_review",
    SessionStatus.DONE.value: "completed",
    SessionStatus.COMPLETED.value: "completed",
    SessionStatus.FAILED.value: "failed",
    SessionStatus.CANCELLED.value: "cancelled",
}


class SessionLike(Protocol):
    id: str
    channel_id: str
    project: str
    status: SessionStatus | str
    goal: str
    description: str
    current_step: Optional[str]
    blocker: Optional[str]
    terminal_ids: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PendingApproval:
    id: str
    session_id: str
    summary: str
    requested_at: Optional[str] = None


@dataclass(frozen=True)
class WaitingTerminal:
    terminal_id: str
    project: str = ""
    session_id: Optional[str] = None
    summary: str = "Terminal waiting for input"


@dataclass
class BriefInput:
    """Injectable snapshot for pure assembly (tests + production)."""

    sessions: list[SessionLike] = field(default_factory=list)
    pending_approvals: list[PendingApproval] = field(default_factory=list)
    waiting_terminals: list[WaitingTerminal] = field(default_factory=list)
    workstream_id: Optional[str] = None
    default_workstream_hint: Optional[str] = None
    generated_at: Optional[datetime] = None


def user_facing_status(status: SessionStatus | str) -> str:
    value = status.value if isinstance(status, SessionStatus) else str(status)
    return _STATUS_LABELS.get(value, value)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def _session_goal(session: SessionLike) -> str:
    return _truncate(session.goal or session.description or "(no goal)", GOAL_LIMIT)


def _session_status_value(session: SessionLike) -> str:
    status = session.status
    return status.value if isinstance(status, SessionStatus) else str(status)


def _sort_key_created(session: SessionLike) -> datetime:
    return session.created_at or datetime.min.replace(tzinfo=None)


def assemble_brief(data: BriefInput) -> dict[str, Any]:
    """Build a structured HomeBase brief from a frozen snapshot.

    Recommendation priority:
      1. Oldest pending approval whose session is in this snapshot
      2. Oldest session whose status is waiting
      3. Oldest running session
      4. Start work on default / idle
    """
    sessions = list(data.sessions)
    if data.workstream_id:
        sessions = [s for s in sessions if s.channel_id == data.workstream_id]

    approvals = sorted(
        data.pending_approvals,
        key=lambda a: a.requested_at or "",
    )
    # Approvals count only for sessions in this snapshot. A pending row whose
    # session is cancelled, failed, or outside the workstream is not "needs you."
    session_ids = {s.id for s in sessions}
    approvals = [a for a in approvals if a.session_id in session_ids]
    approval_session_ids = {a.session_id for a in approvals}

    waiting_sessions = sorted(
        [s for s in sessions if _session_status_value(s) == SessionStatus.WAITING.value],
        key=_sort_key_created,
    )
    running_sessions = sorted(
        [s for s in sessions if _session_status_value(s) == SessionStatus.RUNNING.value],
        key=_sort_key_created,
    )
    other_active = sorted(
        [
            s
            for s in sessions
            if _session_status_value(s)
            in {
                SessionStatus.PENDING.value,
                SessionStatus.VERIFYING.value,
            }
        ],
        key=_sort_key_created,
    )

    needs_you: list[dict[str, Any]] = []
    for approval in approvals:
        needs_you.append(
            {
                "kind": "approval",
                "session_id": approval.session_id,
                "approval_id": approval.id,
                "summary": _truncate(approval.summary, REASON_LIMIT),
            }
        )
    for session in waiting_sessions:
        if session.id in approval_session_ids:
            continue
        reason = session.blocker or session.current_step or "Session waiting for input"
        needs_you.append(
            {
                "kind": "session_blocker",
                "session_id": session.id,
                "summary": _truncate(reason, REASON_LIMIT),
            }
        )

    covered_sessions = {item["session_id"] for item in needs_you if item.get("session_id")}
    for term in data.waiting_terminals:
        # A pinned brief has no workstream for a terminal that is not in a session.
        if data.workstream_id and not term.session_id:
            continue
        if term.session_id and term.session_id in covered_sessions:
            continue
        if term.session_id and term.session_id not in session_ids:
            continue
        needs_you.append(
            {
                "kind": "waiting_terminal",
                "session_id": term.session_id,
                "terminal_id": term.terminal_id,
                "summary": _truncate(
                    term.summary or f"Terminal {term.terminal_id} waiting for input",
                    REASON_LIMIT,
                ),
            }
        )

    active_payload = []
    for session in sorted(sessions, key=_sort_key_created):
        active_payload.append(
            {
                "id": session.id,
                "goal": _session_goal(session),
                "status": user_facing_status(session.status),
                "raw_status": _session_status_value(session),
                "project": session.project,
                "workstream_id": session.channel_id,
                "current_step": _truncate(session.current_step or "", REASON_LIMIT) or None,
                "blocker": _truncate(session.blocker or "", REASON_LIMIT) or None,
            }
        )

    recommended = _recommend(
        approvals=approvals,
        waiting_sessions=waiting_sessions,
        running_sessions=running_sessions,
        other_active=other_active,
        default_workstream_hint=data.default_workstream_hint or data.workstream_id,
    )

    generated = data.generated_at or datetime.now(timezone.utc)
    project_counts: dict[str, int] = {}
    for item in active_payload:
        proj = str(item.get("project") or "unknown")
        project_counts[proj] = project_counts.get(proj, 0) + 1
    spoken = _spoken_summary(
        active_count=len(active_payload),
        needs_you=needs_you,
        recommended=recommended,
        idle_hint=data.default_workstream_hint or data.workstream_id,
        project_counts=project_counts,
    )

    return {
        "homebase": {"label": "HomeBase"},
        "scope": "workstream" if data.workstream_id else "homebase",
        "workstream_id": data.workstream_id,
        "generated_at": generated.isoformat(),
        "needs_you": needs_you,
        "active_sessions": active_payload,
        "recommended_next": recommended,
        "spoken_summary": spoken,
    }


def _recommend(
    *,
    approvals: list[PendingApproval],
    waiting_sessions: list[SessionLike],
    running_sessions: list[SessionLike],
    other_active: list[SessionLike],
    default_workstream_hint: Optional[str],
) -> dict[str, Any]:
    if approvals:
        a = approvals[0]
        return {
            "action": "approve",
            "summary": _truncate(f"Approve or reject: {a.summary}", REASON_LIMIT),
            "session_id": a.session_id,
            "approval_id": a.id,
        }
    if waiting_sessions:
        s = waiting_sessions[0]
        reason = s.blocker or s.current_step or _session_goal(s)
        return {
            "action": "check",
            "summary": _truncate(f"Check: {reason}", REASON_LIMIT),
            "session_id": s.id,
        }
    if running_sessions:
        s = running_sessions[0]
        return {
            "action": "monitor",
            "summary": _truncate(f"Monitor: {_session_goal(s)}", REASON_LIMIT),
            "session_id": s.id,
        }
    if other_active:
        s = other_active[0]
        label = user_facing_status(s.status)
        return {
            "action": "monitor",
            "summary": _truncate(f"Monitor ({label}): {_session_goal(s)}", REASON_LIMIT),
            "session_id": s.id,
        }
    hint = default_workstream_hint or "your default workstream"
    return {
        "action": "start",
        "summary": _truncate(f"Start work on {hint}", REASON_LIMIT),
        "session_id": None,
    }


def _spoken_summary(
    *,
    active_count: int,
    needs_you: list[dict[str, Any]],
    recommended: dict[str, Any],
    idle_hint: Optional[str],
    project_counts: Optional[dict[str, int]] = None,
) -> str:
    if active_count == 0 and not needs_you:
        target = idle_hint or "a workstream"
        text = (
            f"Nothing running on HomeBase. "
            f"Recommended: start a session on {target}."
        )
        return _truncate(text, SPOKEN_LIMIT)

    needs_count = len(needs_you)
    parts = [f"You have {active_count} active session{'s' if active_count != 1 else ''}."]
    if project_counts and len(project_counts) > 1:
        breakdown = ", ".join(
            f"{n} on {proj}" for proj, n in sorted(project_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        )
        parts.append(f"Across projects: {breakdown}.")
    if needs_count:
        first = needs_you[0].get("summary") or "needs attention"
        parts.append(
            f"{needs_count} need{'s' if needs_count == 1 else ''} you: {_truncate(str(first), 120)}."
        )
    else:
        parts.append("Nothing needs you right now.")
    rec = recommended.get("summary") or "No recommendation."
    parts.append(f"Recommended: {rec}")
    return _truncate(" ".join(parts), SPOKEN_LIMIT)


def list_pending_approvals_from_db(db, session_ids: Optional[set[str]] = None) -> list[PendingApproval]:
    """Load pending approvals from the sessions DB."""
    rows = db.execute(
        """SELECT id, session_id, summary, requested_at
           FROM session_approvals
           WHERE status = 'pending'
           ORDER BY requested_at ASC"""
    ).fetchall()
    results: list[PendingApproval] = []
    for row in rows:
        sid = row["session_id"]
        if session_ids is not None and sid not in session_ids:
            continue
        results.append(
            PendingApproval(
                id=row["id"],
                session_id=sid,
                summary=row["summary"] or "Approval required",
                requested_at=row["requested_at"],
            )
        )
    return results


def collect_waiting_terminals(
    terminal_manager,
    sessions: list[SessionLike],
    is_waiting: Optional[Callable[[str], bool]] = None,
) -> list[WaitingTerminal]:
    """Find terminals waiting for input, linked to sessions when possible."""
    check = is_waiting or terminal_manager.is_waiting_for_input
    term_to_session: dict[str, str] = {}
    for session in sessions:
        for tid in session.terminal_ids or []:
            term_to_session[tid] = session.id

    waiting: list[WaitingTerminal] = []
    try:
        terminals = terminal_manager.list()
    except Exception:
        return waiting

    for term in terminals:
        tid = term.id
        try:
            if not check(tid):
                continue
        except Exception:
            continue
        waiting.append(
            WaitingTerminal(
                terminal_id=tid,
                project=getattr(term, "project", "") or "",
                session_id=term_to_session.get(tid),
                summary=f"Terminal waiting for input ({getattr(term, 'project', '') or tid})",
            )
        )
    return waiting


def build_homebase_brief(
    *,
    workstream_id: Optional[str] = None,
    session_manager=None,
    terminal_manager=None,
) -> dict[str, Any]:
    """Production entry: load live state and assemble a brief."""
    if session_manager is None:
        from .session_manager import get_session_manager

        session_manager = get_session_manager()

    sessions = session_manager.list_active()
    approvals = list_pending_approvals_from_db(
        session_manager._db(),
        session_ids={session.id for session in sessions},
    )

    waiting: list[WaitingTerminal] = []
    if terminal_manager is None:
        try:
            from .terminal import get_terminal_manager

            terminal_manager = get_terminal_manager()
        except Exception:
            terminal_manager = None
    if terminal_manager is not None:
        waiting = collect_waiting_terminals(terminal_manager, sessions)

    default_hint = workstream_id
    if not default_hint and sessions:
        # Prefer most recently updated session's workstream as idle hint fallback
        newest = max(sessions, key=lambda s: s.updated_at or s.created_at)
        default_hint = newest.channel_id

    return assemble_brief(
        BriefInput(
            sessions=sessions,
            pending_approvals=approvals,
            waiting_terminals=waiting,
            workstream_id=workstream_id,
            default_workstream_hint=default_hint,
        )
    )
