"""Shared create-and-start path for supervised work sessions.

UI (POST /sessions + retry) and orchestrator spawn_agent must use the same
durable supervised session shape so Sessions Hub, evidence, and policy stay aligned.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from .db.repositories import get_project_repo, resolve_project_id
from .session_manager import (
    Session,
    SessionStatus,
    agent_provider_blocker,
    build_agent_command,
    get_session_manager,
)

logger = logging.getLogger(__name__)


def session_git_snapshot(project: str) -> dict[str, str]:
    try:
        record = get_project_repo().get(project)
        path = Path(record.path) if record and record.path else None
        if not path or not path.is_dir():
            return {"head": "", "status": ""}
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=path,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return {
            "head": head.stdout.strip() if head.returncode == 0 else "",
            "status": status.stdout.strip() if status.returncode == 0 else "",
        }
    except Exception:
        return {"head": "", "status": ""}


def _evidence_provider_refs(project: str) -> list[dict[str, Any]]:
    try:
        from .evidence_providers import active_evidence_provider_refs

        record = get_project_repo().get(project)
        path = Path(record.path) if record and record.path else None
        if path:
            return active_evidence_provider_refs(path)
    except Exception:
        logger.debug("evidence provider discovery failed for %s", project, exc_info=True)
    return [{"id": "native", "label": "RDC native evidence"}]


def _load_agent_provider_policy() -> dict[str, str]:
    """Mirror app policy loader without importing app (circular)."""
    import json

    from .config import get_rdc_home

    path = get_rdc_home() / "agent_provider_policy.json"
    try:
        policy = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        policy = {}
    preferred = str(policy.get("preferred_provider") or "codex")
    if preferred not in {"codex", "claude", "cursor"}:
        preferred = "codex"
    return {
        "preferred_provider": preferred,
        "fallback_policy": str(policy.get("fallback_policy") or "ask"),
    }


def session_execution_prompt(session: Session) -> str:
    inherited = (session.metadata or {}).get("inherited_context")
    followups = (session.metadata or {}).get("followup_instructions") or []
    followup_context = ""
    if isinstance(followups, list) and followups:
        turns = []
        for item in followups[-10:]:
            if not isinstance(item, dict):
                continue
            prior = str(item.get("prior_result") or "").strip()
            instruction = str(item.get("instruction") or "").strip()
            turns.append(
                f"Prior agent outcome:\n{prior[:8000]}\n\nUser follow-up:\n{instruction[:4000]}"
            )
        if turns:
            followup_context = (
                "\n\nThis is a continued conversation within the same RDC Session. "
                "Native provider resume was not available, so RDC reconstructed the durable "
                "conversation below. Inspect the repository to confirm current state, "
                "then carry out the latest user instruction.\n\n"
                + "\n\n---\n\n".join(turns)
            )
    if not isinstance(inherited, dict):
        return (session.goal or session.description) + followup_context
    evidence = inherited.get("evidence") or []
    evidence_summary = "\n".join(
        f"- {item.get('title', item.get('type', 'Evidence'))} ({item.get('id', 'unknown')})"
        for item in evidence
        if isinstance(item, dict)
    ) or "- No independent evidence was attached."
    return (
        f"{session.goal or session.description}\n\n"
        "This Session continues approved work from a parent Session. Use the handoff below as durable context; "
        "inspect the repository and verify assumptions before changing files.\n\n"
        f"Parent goal:\n{str(inherited.get('parent_goal') or '')[:4000]}\n\n"
        f"Parent result / artifact:\n{str(inherited.get('parent_result') or '')[:12000]}\n\n"
        f"Inherited evidence references:\n{evidence_summary}{followup_context}"
    )


def create_supervised_session(
    *,
    channel_id: str,
    project: str,
    goal: str,
    provider: Optional[str] = None,
    acceptance_criteria: Optional[list[str]] = None,
    policy: Optional[dict] = None,
    budget: Optional[dict] = None,
    metadata_extra: Optional[dict] = None,
) -> Session:
    """Create a durable supervised session (pending) with evidence/git baseline metadata."""
    if not resolve_project_id(project):
        raise ValueError(f"Project not found: {project}")

    metadata: dict[str, Any] = {
        "supervised": True,
        "git_baseline": session_git_snapshot(project),
        "evidence_providers": _evidence_provider_refs(project),
    }
    if metadata_extra:
        metadata.update(metadata_extra)

    sm = get_session_manager()
    return sm.create_session(
        channel_id=channel_id,
        project=project,
        description=goal,
        agent_provider=provider,
        metadata=metadata,
        acceptance_criteria=[item.strip() for item in (acceptance_criteria or []) if item.strip()],
        policy=policy or {},
        budget=budget,
    )


_RERUN_STATUSES = {SessionStatus.FAILED.value, SessionStatus.CANCELLED.value}
_REVIEW_STATUSES = {
    SessionStatus.VERIFYING.value,
    SessionStatus.COMPLETED.value,
    SessionStatus.DONE.value,
}


def _fail_claimed_start(sm, session: Session, previous: str, *, summary: str, error: str) -> None:
    """A failed resume of a review must put that review back."""
    if previous in _REVIEW_STATUSES:
        session.blocker = None
        sm.update_status(session.id, SessionStatus(previous), output_summary=session.output_summary)
        raise RuntimeError(error)
    session.blocker = summary
    sm._persist(session)
    sm.update_status(session.id, SessionStatus.FAILED, output_summary=summary)
    raise RuntimeError(error)


def start_supervised_session(
    session_id: str,
    *,
    provider: Optional[str] = None,
    continue_review: bool = False,
) -> dict[str, Any]:
    """Start or re-run a supervised session agent terminal (shared with UI retry)."""
    from .channel_manager import get_channel_manager
    from .terminal import get_terminal_manager

    sm = get_session_manager()
    s = sm.get(session_id)
    if not s:
        raise LookupError(f"Session not found: {session_id}")

    if provider:
        s.agent_provider = provider
        sm._persist(s)

    previous = sm.claim_for_start(s.id, continue_review=continue_review)
    if previous is None:
        fresh = sm.get(session_id)
        label = fresh.status.value if fresh and isinstance(fresh.status, SessionStatus) else "missing"
        raise ValueError(f"Cannot start session in status: {label}")

    tm = get_terminal_manager()
    if previous in _RERUN_STATUSES or previous in _REVIEW_STATUSES:
        sm.stop_monitor(s.id)
        for terminal_id in list(s.terminal_ids):
            try:
                tm.destroy(terminal_id)
            except Exception:
                pass
        sm._db().execute("DELETE FROM session_terminals WHERE session_id = ?", (s.id,))
        sm._db().commit()
        s.terminal_ids = []
        s.control_owner = "none"
        sm._persist(s)
        sm.log_event(s, "session.run_again" if previous in _RERUN_STATUSES else "session.continued", {})

    requested_provider = s.agent_provider or "orchestrated"
    effective = (
        _load_agent_provider_policy()["preferred_provider"]
        if requested_provider == "orchestrated"
        else requested_provider
    )
    provider_blocker = agent_provider_blocker(effective)
    if provider_blocker:
        if requested_provider == "orchestrated":
            provider_blocker = (
                f"Preferred provider {effective} is not ready. {provider_blocker} "
                "Configure it or explicitly choose another provider."
            )
        _fail_claimed_start(
            sm,
            s,
            previous,
            summary=provider_blocker,
            error=provider_blocker,
        )

    command = build_agent_command(effective, session_execution_prompt(s))
    s.metadata = {
        **(s.metadata or {}),
        "requested_provider": requested_provider,
        "effective_provider": effective,
        "supervised": True if (s.metadata or {}).get("supervised", True) else False,
    }
    # Always mark supervised for this path
    s.metadata["supervised"] = True
    sm._persist(s)

    try:
        term = tm.create(project=s.project, command=command)
    except Exception as e:
        logger.warning("Supervised session %s failed before the terminal existed: %s", s.id, type(e).__name__)
        try:
            _fail_claimed_start(
                sm,
                s,
                previous,
                summary="Could not start the agent.",
                error="Could not start the agent.",
            )
        except RuntimeError:
            raise RuntimeError("Could not start the agent.") from e

    sm.link_terminal(s.id, term.id)
    try:
        cm = get_channel_manager()
        cm.post_message(
            s.channel_id,
            role="system",
            content=f"Session started: {(s.goal or s.description)[:200]}",
            metadata={
                "type": "a2ui",
                "components": [
                    {
                        "type": "task_card",
                        "title": (s.goal or s.description)[:80],
                        "status": "running",
                        "project": s.project,
                        "description": f"Session {s.id}",
                    },
                ],
            },
        )
        cm.emit_event(
            "session.started",
            channel_id=s.channel_id,
            project_id=s.project,
            data={"session_id": s.id, "terminal_id": term.id},
        )
    except Exception:
        logger.warning("Session %s is running but the channel post failed", s.id, exc_info=True)
    return {
        "success": True,
        "session_id": s.id,
        "terminal_id": term.id,
        "provider": effective,
    }


def create_and_start_supervised_session(
    *,
    channel_id: str,
    project: str,
    goal: str,
    provider: Optional[str] = None,
    acceptance_criteria: Optional[list[str]] = None,
    policy: Optional[dict] = None,
    budget: Optional[dict] = None,
    metadata_extra: Optional[dict] = None,
) -> dict[str, Any]:
    """Create supervised session and start the agent — single path for UI + voice."""
    session = create_supervised_session(
        channel_id=channel_id,
        project=project,
        goal=goal,
        provider=provider or "orchestrated",
        acceptance_criteria=acceptance_criteria,
        policy=policy,
        budget=budget,
        metadata_extra=metadata_extra,
    )
    start = start_supervised_session(session.id)
    return {
        "session": session,
        "session_id": session.id,
        "terminal_id": start.get("terminal_id"),
        "provider": start.get("provider"),
        "success": True,
    }
