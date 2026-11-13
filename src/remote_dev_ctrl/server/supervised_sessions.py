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


def session_git_snapshot(project: str, override_path: Optional[Path] = None) -> dict[str, str]:
    try:
        path = override_path
        if not path:
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
    """Mirror app policy loader via unified agent registry."""
    from .agent_registry import load_agent_provider_policy

    return load_agent_provider_policy()


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
    status: Optional[str] = None,
    task_id: Optional[str] = None,
    worktree_mode: Optional[str] = None,
    worktree_branch: Optional[str] = None,
    discovered_from: Optional[str] = None,
) -> Session:
    """Create a durable supervised session with evidence/git baseline metadata."""
    if not resolve_project_id(project):
        raise LookupError(f"Project not found: {project}")

    sm = get_session_manager()
    db = sm._db()

    # #system is the scratch workstream and may own sessions.
    ch_row = db.execute("SELECT id, type, collection_id FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not ch_row:
        raise LookupError(f"Channel not found: {channel_id}")

    # If the channel is not system, the project's collection must equal the channel's.
    is_system = ch_row["id"] == "ch-system" or ch_row["type"] == "system"
    if not is_system:
        proj_obj = get_project_repo().get(project)
        p_coll = (proj_obj.collection_id if proj_obj else None) or "general"
        ch_coll = ch_row["collection_id"] or "general"
        if p_coll != ch_coll:
            raise ValueError(f"Project collection '{p_coll}' does not match channel collection '{ch_coll}'")

    # Channel-project constraints: if channel has linked projects, project must be one of them
    cp_rows = db.execute(
        "SELECT p.name, p.id FROM projects p JOIN channel_projects cp ON cp.project_id = p.id WHERE cp.channel_id = ?",
        (channel_id,),
    ).fetchall()
    if cp_rows:
        linked_names = {r["name"] for r in cp_rows} | {r["id"] for r in cp_rows}
        if project not in linked_names:
            raise ValueError(f"Project '{project}' is not linked to channel '{channel_id}'")

    # Resolve concrete agent provider: explicit -> collection default -> preferred_provider
    requested = (provider or "").strip()
    if requested and requested != "orchestrated":
        concrete_provider = requested
    else:
        coll_default = None
        if ch_row["collection_id"]:
            c_row = db.execute(
                "SELECT default_agent_id FROM collections WHERE id = ?",
                (ch_row["collection_id"],),
            ).fetchone()
            if c_row and c_row["default_agent_id"]:
                coll_default = c_row["default_agent_id"].strip()
        concrete_provider = coll_default or _load_agent_provider_policy().get("preferred_provider", "codex")

    # Resolve workspace mode and branch
    parent_id = discovered_from or (metadata_extra.get("parent_session_id") if metadata_extra else None)
    effective_worktree_mode = worktree_mode or "isolated"
    effective_worktree_path = None
    effective_worktree_branch = worktree_branch

    if parent_id:
        parent_sess = sm.get(parent_id)
        if parent_sess:
            if worktree_mode is None:
                # Inherit from parent: if parent used worktree, reuse it
                if parent_sess.worktree_mode == "isolated" or parent_sess.worktree_path:
                    effective_worktree_mode = "reuse"
                    effective_worktree_path = parent_sess.worktree_path
                    effective_worktree_branch = parent_sess.worktree_branch
                else:
                    effective_worktree_mode = "direct"
                    effective_worktree_path = parent_sess.worktree_path
                    effective_worktree_branch = parent_sess.worktree_branch
            elif worktree_mode == "reuse":
                effective_worktree_path = parent_sess.worktree_path
                effective_worktree_branch = parent_sess.worktree_branch or worktree_branch

    metadata: dict[str, Any] = {
        "supervised": True,
        "git_baseline": session_git_snapshot(project),
        "evidence_providers": _evidence_provider_refs(project),
    }
    if metadata_extra:
        metadata.update(metadata_extra)

    session_status = SessionStatus.PROPOSED if status == "proposed" else (SessionStatus(status) if status else SessionStatus.PENDING)

    return sm.create_session(
        channel_id=channel_id,
        project=project,
        description=goal,
        agent_provider=concrete_provider,
        task_id=task_id,
        metadata=metadata,
        acceptance_criteria=[item.strip() for item in (acceptance_criteria or []) if item.strip()],
        policy=policy or {},
        budget=budget,
        status=session_status,
        worktree_mode=effective_worktree_mode,
        worktree_path=effective_worktree_path,
        worktree_branch=effective_worktree_branch,
        discovered_from=parent_id,
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


def _status_label(session: Session) -> str:
    return session.status.value if isinstance(session.status, SessionStatus) else str(session.status)


def _paths_equal(left: str | Path | None, right: str | Path | None) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _checkout_blocker(project: str, candidate: Path, project_root: Path, exclude_session_id: str) -> Session | None:
    """Another started session already writing this checkout path.

    A pending session is created but has not claimed a checkout. Start moves
    the session to running before this check, so a staged peer does not reserve
    the project root. A started direct session still blocks that root when its
    path was never stored.
    """
    candidate_is_root = _paths_equal(candidate, project_root)
    for other in get_session_manager().list_active():
        if other.id == exclude_session_id or other.project != project:
            continue
        if _status_label(other) == SessionStatus.PENDING.value:
            continue
        if _paths_equal(other.worktree_path, candidate):
            return other
        if candidate_is_root and (other.worktree_mode or "") == "direct":
            return other
    return None


def _busy_message(blocker: Session) -> str:
    return (
        f"Checkout is in use by session {blocker.id} ({_status_label(blocker)}). "
        "Wait for it to finish or cancel it before starting another writer on this path."
    )


def _try_claim_project_root(sm, session: Session, project_path: Path, wm) -> str | None:
    """Reserve the live checkout. Returns a refusal message, or None when claimed."""
    blocker = _checkout_blocker(session.project, project_path, project_path, session.id)
    if blocker is not None:
        return _busy_message(blocker)
    session.worktree_mode = "direct"
    session.worktree_path = str(project_path.resolve())
    if wm.is_git_repo(project_path):
        session.worktree_branch = wm.get_current_branch(project_path)
    sm._persist(session)
    return None


def _assign_checkout(sm, session: Session, project_path: Path, wm, previous: str) -> Path:
    """Choose the session directory and persist that claim before the terminal starts.

    An isolated session whose worktree cannot be created fails. It does not switch
    to the live project checkout. The check and the claim share the rdc database lock.
    """
    from .db.connection import get_db_manager

    is_git = wm.is_git_repo(project_path)
    sm._db()
    lock = get_db_manager().get_lock("rdc")
    refusal: str | None = None
    create_isolated = False
    claimed: Path | None = None
    with lock:
        mode = session.worktree_mode or "isolated"
        existing = Path(session.worktree_path) if session.worktree_path else None
        existing_ready = bool(existing and existing.is_dir())
        on_project_root = existing_ready and _paths_equal(existing, project_path)

        if (not is_git) or mode == "direct" or on_project_root:
            refusal = _try_claim_project_root(sm, session, project_path, wm)
            if refusal is None:
                claimed = project_path.resolve()
        elif mode == "reuse":
            if not existing_ready or existing is None:
                refusal = (
                    "The isolated worktree for this session is missing. "
                    "The session was not started on the project checkout."
                )
            else:
                blocker = _checkout_blocker(session.project, existing, project_path, session.id)
                refusal = _busy_message(blocker) if blocker is not None else None
                if refusal is None:
                    sm._persist(session)
                    claimed = existing.resolve()
        else:
            if existing_ready and existing is not None:
                blocker = _checkout_blocker(session.project, existing, project_path, session.id)
                refusal = _busy_message(blocker) if blocker is not None else None
                if refusal is None:
                    sm._persist(session)
                    claimed = existing.resolve()
            else:
                planned = wm.worktree_path_for(project_path, session.id)
                blocker = _checkout_blocker(session.project, planned, project_path, session.id)
                if blocker is not None:
                    refusal = _busy_message(blocker)
                else:
                    session.worktree_mode = "isolated"
                    session.worktree_path = str(planned)
                    sm._persist(session)
                    create_isolated = True

    if refusal:
        _fail_claimed_start(sm, session, previous, summary=refusal, error=refusal)
    if create_isolated:
        try:
            wt_path, wt_branch = wm.create_worktree(
                project_path,
                session.id,
                branch_name=session.worktree_branch,
            )
        except Exception as exc:
            logger.warning(
                "Could not create worktree for session %s: %s",
                session.id,
                type(exc).__name__,
            )
            wt_path, wt_branch = None, None
        if not wt_path:
            message = (
                "Could not create an isolated worktree. "
                "The session was not started on the project checkout."
            )
            _fail_claimed_start(sm, session, previous, summary=message, error=message)
            raise RuntimeError(message)
        session.worktree_path = str(wt_path)
        session.worktree_branch = wt_branch
        sm._persist(session)
        claimed = wt_path
    if claimed is None:
        message = "Could not choose a checkout for this session."
        _fail_claimed_start(sm, session, previous, summary=message, error=message)
        raise RuntimeError(message)
    return claimed


def assign_staged_agent(session_id: str, provider: str) -> Session:
    """Change the agent on a staged goal. The goal stays staged."""
    sm = get_session_manager()
    session = sm.get(session_id)
    if not session:
        raise LookupError(f"Session not found: {session_id}")
    status = session.status.value if isinstance(session.status, SessionStatus) else str(session.status)
    if status != SessionStatus.PROPOSED.value:
        raise RuntimeError("Agent can be changed only while the goal is staged")

    requested = (provider or "").strip()
    if not requested:
        raise ValueError("Provider is required")
    if requested != "orchestrated" and requested != (session.agent_provider or ""):
        from .agent_registry import list_registered_wrappers

        known = {wrapper.id for wrapper in list_registered_wrappers() if wrapper.supports_sessions}
        if requested not in known:
            raise ValueError(f"Unknown agent: {requested}")

    session.agent_provider = requested
    sm._persist(session)
    sm.log_event(session, "session.agent_assigned", {"provider": requested})
    return session


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
    if requested_provider in (None, "", "orchestrated"):
        coll_default = None
        ch_row = sm._db().execute("SELECT collection_id FROM channels WHERE id = ?", (s.channel_id,)).fetchone()
        if ch_row and ch_row["collection_id"]:
            c_row = sm._db().execute("SELECT default_agent_id FROM collections WHERE id = ?", (ch_row["collection_id"],)).fetchone()
            if c_row and c_row["default_agent_id"]:
                coll_default = c_row["default_agent_id"].strip()
        effective = coll_default or _load_agent_provider_policy().get("preferred_provider", "codex")
        s.agent_provider = effective
        sm._persist(s)
    else:
        effective = requested_provider

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

    rdc_note = (
        f"\n\n[RDC Execution Note: Leave modified files uncommitted in the working tree "
        f"so the operator can inspect the diff and commit from RDC in 1 click. "
        f"If this goal benefits from a rich custom visual report, benchmark table, or architecture diagram, "
        f"you may optionally write a standalone dark-mode HTML file to .rdc/sessions/{s.id}.html.]"
    )
    followups = (s.metadata or {}).get("followup_instructions") or []
    latest_instruction = (
        str(followups[-1].get("instruction") or "").strip()
        if continue_review and isinstance(followups, list) and followups and isinstance(followups[-1], dict)
        else ""
    )
    s.metadata = {
        **(s.metadata or {}),
        "requested_provider": requested_provider,
        "effective_provider": effective,
        "supervised": True if (s.metadata or {}).get("supervised", True) else False,
    }
    # Always mark supervised for this path
    s.metadata["supervised"] = True

    from .worktree import get_worktree_manager

    wm = get_worktree_manager()
    record = get_project_repo().get(s.project)
    project_path = Path(record.path).resolve() if record and record.path else None

    if project_path:
        target_cwd: Optional[Path] = _assign_checkout(sm, s, project_path, wm, previous)
    else:
        s.worktree_mode = "direct"
        target_cwd = None

    if target_cwd:
        s.metadata["git_baseline"] = session_git_snapshot(s.project, override_path=target_cwd)
    sm._persist(s)

    from .knowledge_bridge import (
        fit_goal_first,
        live_state_block,
        pack_for_session,
        pack_pointer_line,
        safe_pointer_or_empty,
        scrub_goal,
        vault_extra_secrets,
        write_pack_file,
    )

    secrets = vault_extra_secrets()
    pack = pack_for_session(s, extra_secrets=secrets)
    live = live_state_block(s)
    path = write_pack_file(s.id, pack.text, live)
    goal = scrub_goal(session_execution_prompt(s), secrets)
    instruction = scrub_goal(latest_instruction, secrets) if latest_instruction else ""
    note = rdc_note
    pointer = pack_pointer_line(path) if path else ""
    if effective == "shell":
        base = instruction if instruction else (goal + note)
        command = build_agent_command(effective, base + safe_pointer_or_empty(path))
    elif instruction:
        sent = fit_goal_first(instruction, pointer, limit=4000)
        fallback = fit_goal_first(goal, note, pointer, limit=4000)
        command = build_agent_command(effective, sent, resume=True, fallback_description=fallback)
    else:
        command = build_agent_command(effective, fit_goal_first(goal, note, pointer, limit=4000))

    try:
        term = tm.create(project=s.project, command=command, cwd=str(target_cwd) if target_cwd else None)
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
