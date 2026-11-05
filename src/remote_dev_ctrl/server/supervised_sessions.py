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


def _same_checkout(path: str | None, root: Path) -> bool:
    if not path:
        return False
    try:
        return Path(path).resolve() == root.resolve()
    except OSError:
        return False


def _checkout_blocker(project: str, project_root: Path, exclude_session_id: str) -> Session | None:
    """Another active session already writing this project directory."""
    for other in get_session_manager().list_active():
        if other.id == exclude_session_id or other.project != project:
            continue
        if (other.worktree_mode or "") == "direct" or _same_checkout(other.worktree_path, project_root):
            return other
    return None


def _refuse_if_checkout_busy(sm, session: Session, project_root: Path, previous: str) -> None:
    blocker = _checkout_blocker(session.project, project_root, session.id)
    if blocker is None:
        return
    message = (
        f"Checkout is in use by session {blocker.id} ({_status_label(blocker)}). "
        "Wait for it to finish or cancel it before starting another writer on this path."
    )
    _fail_claimed_start(sm, session, previous, summary=message, error=message)


def _use_project_checkout(sm, session: Session, project_path: Path, wm, previous: str) -> Path:
    """Point a session at the project directory when that checkout is free."""
    _refuse_if_checkout_busy(sm, session, project_path, previous)
    session.worktree_mode = "direct"
    session.worktree_path = str(project_path)
    if wm.is_git_repo(project_path):
        session.worktree_branch = wm.get_current_branch(project_path)
    return project_path


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

    target_cwd: Optional[Path] = None
    if project_path and wm.is_git_repo(project_path):
        mode = s.worktree_mode or "isolated"
        if mode == "isolated":
            if s.worktree_path and Path(s.worktree_path).is_dir():
                target_cwd = Path(s.worktree_path)
            else:
                _refuse_if_checkout_busy(sm, s, project_path, previous)
                try:
                    wt_path, wt_branch = wm.create_worktree(
                        project_path,
                        s.id,
                        branch_name=s.worktree_branch,
                    )
                    if wt_path:
                        s.worktree_path = str(wt_path)
                        s.worktree_branch = wt_branch
                        target_cwd = wt_path
                except Exception as e:
                    logger.warning("Could not create worktree for session %s: %s", s.id, type(e).__name__)
                    target_cwd = _use_project_checkout(sm, s, project_path, wm, previous)
        elif mode == "reuse":
            if s.worktree_path and Path(s.worktree_path).is_dir():
                target_cwd = Path(s.worktree_path)
            else:
                target_cwd = _use_project_checkout(sm, s, project_path, wm, previous)
        else:  # direct
            target_cwd = _use_project_checkout(sm, s, project_path, wm, previous)
    elif project_path:
        target_cwd = _use_project_checkout(sm, s, project_path, wm, previous)
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
