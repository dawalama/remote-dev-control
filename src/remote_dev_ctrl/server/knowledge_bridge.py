"""Knowledge bridge connecting RDC server to the corpusnote library.

Single point of entry for corpusnote. Swallows exceptions gracefully so vault
issues never crash session startup or lifecycle transitions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .channel_manager import get_channel_manager
from .config import Config, get_rdc_home
from .db.repositories import get_project_repo

logger = logging.getLogger(__name__)

_SAFE_PATH_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_.-")


class KnowledgeDisabledError(Exception):
    """Raised when an operation is requested while knowledge.enabled is false."""


class RejectedWrite(Exception):
    """Re-exported or wrapped RejectedWrite error from corpusnote."""


class MissingPage(Exception):
    """Re-exported or wrapped MissingPage error from corpusnote."""


def _get_knowledge_root() -> Path:
    cfg = Config.load()
    if cfg.knowledge.root:
        return Path(cfg.knowledge.root).resolve()
    return (get_rdc_home() / "knowledge").resolve()


def is_knowledge_enabled() -> bool:
    try:
        cfg = Config.load()
        if not cfg.knowledge.enabled:
            return False
        import corpusnote  # noqa: F401
        return True
    except Exception:
        return False


def vault_extra_secrets() -> tuple[str, ...]:
    """Load secrets from the RDC vault for query and prompt scrubbing."""
    try:
        from .vault import get_vault
        v = get_vault()
        secrets = []
        for key in v.list_keys():
            val = v.get(key)
            if val and isinstance(val, str) and len(val.strip()) > 3:
                secrets.append(val.strip())
        return tuple(secrets)
    except Exception as exc:
        logger.warning("vault_unreadable: %s", type(exc).__name__)
        return ()


def corpus_id_for_pack(project: str | None, workstream_id: str | None) -> str | None:
    """Resolve collection corpus ID for a session or orchestrator call."""
    if project:
        p = get_project_repo().get(project)
        if p and p.collection_id:
            return p.collection_id
    if workstream_id and workstream_id not in ("ch-system", "#system"):
        ch = get_channel_manager().get_channel(workstream_id)
        if ch:
            return ch.collection_id or "general"
    return None


def _get_corpus_handle(corpus_id: str):
    import corpusnote
    root = _get_knowledge_root()
    return corpusnote.open_corpus(
        corpus_id,
        markdown_root=root / "corpora",
        qmd_config=root / "qmd" / corpus_id / "config",
        qmd_cache=root / "qmd" / corpus_id / "cache",
    )


def pack_for_session(session: Any, *, extra_secrets: tuple[str, ...] | None = None) -> Any:
    """Assemble budgeted knowledge pack for a supervised session start."""
    import corpusnote
    empty_pack = corpusnote.Pack(sections=[], text="", tokens=0, omitted=[], warnings=[])

    if not is_knowledge_enabled():
        return empty_pack

    corpus_id = corpus_id_for_pack(session.project, session.channel_id)
    if not corpus_id:
        return empty_pack

    secrets = vault_extra_secrets() if extra_secrets is None else extra_secrets

    goal = (
        getattr(session, "execution_prompt", None)
        or getattr(session, "goal", None)
        or getattr(session, "user_prompt", None)
        or ""
    )

    parent_id = session.discovered_from or (session.metadata or {}).get("parent_session_id")

    citations: list[str] = []
    if session.project:
        p = get_project_repo().get(session.project)
        if p and p.path:
            p_dir = Path(p.path).resolve()
            if (p_dir / "AGENTS.md").is_file():
                citations.append(str(p_dir / "AGENTS.md"))
            if (p_dir / ".ai" / "rules.md").is_file():
                citations.append(str(p_dir / ".ai" / "rules.md"))
            if hasattr(p, "profile") and p.profile and getattr(p.profile, "test_command", None):
                citations.append(f"Test command: {p.profile.test_command}")

    try:
        corpus = _get_corpus_handle(corpus_id)
        scrubbed_goal, _ = corpusnote.scrub_text(goal, secrets)
        req = corpusnote.PackRequest(
            goal=scrubbed_goal,
            project=session.project,
            workstream_id=session.channel_id,
            parent_session_id=parent_id,
            budget_tokens=2000,
            include_hits=True,
            extra_secrets=secrets,
        )
        return corpusnote.assemble_pack(corpus, req, repo_citations=citations)
    except Exception as exc:
        logger.warning("knowledge pack_for_session failed: %s", type(exc).__name__)
        return empty_pack


def pack_for_orchestrator(
    project: str | None,
    workstream_id: str | None,
    goal: str,
    *,
    is_local: bool = False,
    extra_secrets: tuple[str, ...] | None = None,
) -> Any:
    """Assemble budgeted knowledge pack for the orchestrator."""
    import corpusnote
    empty_pack = corpusnote.Pack(sections=[], text="", tokens=0, omitted=[], warnings=[])

    if not is_knowledge_enabled():
        return empty_pack

    corpus_id = corpus_id_for_pack(project, workstream_id)
    if not corpus_id:
        return empty_pack

    secrets = vault_extra_secrets() if extra_secrets is None else extra_secrets

    citations: list[str] = []
    if project:
        p = get_project_repo().get(project)
        if p and p.path:
            p_dir = Path(p.path).resolve()
            if (p_dir / "AGENTS.md").is_file():
                citations.append(str(p_dir / "AGENTS.md"))
            if (p_dir / ".ai" / "rules.md").is_file():
                citations.append(str(p_dir / ".ai" / "rules.md"))
            if hasattr(p, "profile") and p.profile and getattr(p.profile, "test_command", None):
                citations.append(f"Test command: {p.profile.test_command}")

    budget = 1000 if is_local else 2000
    include_hits = False if is_local else True

    try:
        corpus = _get_corpus_handle(corpus_id)
        scrubbed_goal, _ = corpusnote.scrub_text(goal, secrets)
        req = corpusnote.PackRequest(
            goal=scrubbed_goal,
            project=project,
            workstream_id=workstream_id,
            parent_session_id=None,
            budget_tokens=budget,
            include_hits=include_hits,
            extra_secrets=secrets,
        )
        return corpusnote.assemble_pack(corpus, req, repo_citations=citations)
    except Exception as exc:
        logger.warning("knowledge pack_for_orchestrator failed: %s", type(exc).__name__)
        return empty_pack


def live_state_block(session: Any) -> str:
    """Build live execution state block (capped at 400 tokens / 1600 chars)."""
    try:
        lines = ["## Live State"]
        if getattr(session, "worktree_path", None):
            lines.append(f"- Worktree path: {session.worktree_path}")
        if getattr(session, "worktree_branch", None):
            lines.append(f"- Worktree branch: {session.worktree_branch}")

        if session.project:
            try:
                from .process_manager import get_process_manager
                pm = get_process_manager()
                procs = pm.list_processes(session.project)
                for pr in procs:
                    if pr.status == "running" and pr.port:
                        lines.append(f"- Action {pr.name} running on port {pr.port}")
            except Exception:
                pass

        if len(lines) == 1:
            return ""

        full = "\n".join(lines)
        # Cap at 1600 chars (~400 tokens)
        return full[:1600]
    except Exception:
        return ""


def write_pack_file(session_id: str, pack_text: str, live_state: str) -> Path | None:
    """Write pack file mode 0600 under runtime/{session_id}.md."""
    if not is_knowledge_enabled():
        return None

    body_parts = [p.strip() for p in (pack_text, live_state) if p and p.strip()]
    if not body_parts:
        return None

    root = _get_knowledge_root()
    runtime_dir = root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    pack_path = runtime_dir / f"{session_id}.md"
    try:
        resolved = pack_path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return None

        content = "\n\n".join(body_parts) + "\n"
        fd = os.open(pack_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return pack_path
    except Exception as exc:
        logger.warning("write_pack_file failed for %s: %s", session_id, type(exc).__name__)
        return None


def pack_pointer_line(path: Path | None) -> str:
    if not path:
        return ""
    return f"\nCollection knowledge available at {path.resolve()}"


def safe_pointer_or_empty(path: Path | None) -> str:
    if not path:
        return ""
    resolved_str = str(path.resolve())
    if any(c not in _SAFE_PATH_CHARS for c in resolved_str):
        return ""
    return pack_pointer_line(path)


def fit_goal_first(goal: str, *extras: str, limit: int = 4000) -> str:
    """Keep the whole pointer and head of goal inside limit."""
    extras_clean = [e for e in extras if e]
    extras_len = sum(len(e) for e in extras_clean)

    avail_for_goal = max(0, limit - extras_len)
    goal_trimmed = goal[:avail_for_goal]

    parts = [goal_trimmed] + extras_clean
    return "".join(parts)


def scrub_goal(goal: str, extra_secrets: tuple[str, ...]) -> str:
    try:
        from corpusnote import scrub_text
        scrubbed, _ = scrub_text(goal, extra_secrets)
        return scrubbed
    except Exception:
        return goal


def capture_completed(session: Any) -> None:
    """Capture completed session notes and handoff into collection corpus."""
    if not is_knowledge_enabled():
        return

    corpus_id = corpus_id_for_pack(session.project, session.channel_id)
    if not corpus_id:
        return

    secrets = vault_extra_secrets()

    goal = (
        getattr(session, "execution_prompt", None)
        or getattr(session, "goal", None)
        or getattr(session, "user_prompt", None)
        or ""
    )

    summary = session.output_summary or ""

    # Latest verification evidence
    exit_code: int | None = None
    passed: bool | None = None
    try:
        from .db.connection import get_db_manager
        with get_db_manager().session("rdc") as db:
            row = db.execute(
                """SELECT exit_code FROM session_evidence 
                   WHERE session_id = ? AND evidence_type = 'verification'
                   ORDER BY created_at DESC LIMIT 1""",
                (session.id,),
            ).fetchone()
            if row:
                exit_code = row["exit_code"]
                passed = (exit_code == 0)
    except Exception:
        pass

    import corpusnote
    corpus = _get_corpus_handle(corpus_id)
    p_rec = get_project_repo().get(session.project) if session.project else None
    proj_uuid = p_rec.id if p_rec else ""

    item = corpusnote.Capture(
        session_id=session.id,
        project=session.project or "",
        workstream_id=session.channel_id,
        goal=goal,
        output_summary=summary,
        verification=corpusnote.Verification(exit_code=exit_code, passed=passed),
        project_id=proj_uuid,
    )
    corpusnote.capture(corpus, item, extra_secrets=secrets)


def schedule_index_update(session: Any) -> None:
    """Schedule qmd index update asynchronously without blocking."""
    if not is_knowledge_enabled():
        return

    corpus_id = corpus_id_for_pack(session.project, session.channel_id)
    if not corpus_id:
        return

    try:
        import corpusnote
        corpus = _get_corpus_handle(corpus_id)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(asyncio.to_thread(corpusnote.update_index, corpus))

            def _on_done(t):
                if t.cancelled():
                    return
                exc = t.exception()
                if exc:
                    logger.warning("qmd_update_failed: %s", type(exc).__name__)

            task.add_done_callback(_on_done)
        except RuntimeError:
            logger.info("qmd_update_deferred (no running asyncio event loop)")
    except Exception as exc:
        logger.warning("schedule_index_update failed: %s", type(exc).__name__)


def move_pages(project_id: str, dst_collection_id: str) -> None:
    """Move project markdown pages to dst_collection_id."""
    if not is_knowledge_enabled():
        return

    import corpusnote
    root = _get_knowledge_root()
    corpusnote.move_project_pages(root / "corpora", dst_collection_id, project_id)


def move_workstream_page(channel_id: str, src_collection_id: str, dst_collection_id: str) -> None:
    """Move workstream markdown page from src to dst collection."""
    if not is_knowledge_enabled():
        return

    import corpusnote
    root = _get_knowledge_root()
    corpusnote.move_workstream_page(root / "corpora", channel_id, src_collection_id, dst_collection_id)


def reconcile_workstream_page(channel_id: str, collection_id: str) -> bool:
    """Locate all copies of workstreams/{channel_id}.md and move to collection_id.

    Returns True if no copy remains outside collection_id.
    """
    if not is_knowledge_enabled():
        return True

    import corpusnote
    root = _get_knowledge_root()
    corpora_dir = root / "corpora"

    located = corpusnote.locate_workstream_corpora(corpora_dir, channel_id)
    all_ok = True
    for src_id in located:
        if src_id != collection_id:
            res = corpusnote.move_workstream_page(corpora_dir, channel_id, src_id, collection_id)
            if res is None:
                all_ok = False

    # Check if any copy still exists outside collection_id
    remaining = corpusnote.locate_workstream_corpora(corpora_dir, channel_id)
    for rem in remaining:
        if rem != collection_id:
            return False

    return all_ok


def pending_workstream_ids(project_id: str) -> list[str]:
    """Read pending channel IDs from {root}/runtime/pending-workstreams/{project_id}."""
    if not is_knowledge_enabled():
        return []

    p_file = _get_knowledge_root() / "runtime" / "pending-workstreams" / project_id
    if not p_file.is_file():
        return []

    try:
        lines = p_file.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip()]
    except Exception:
        return []


def remember_pending_workstream(project_id: str, channel_id: str) -> None:
    """Remember a channel id that failed to move for a project."""
    if not is_knowledge_enabled():
        return

    p_dir = _get_knowledge_root() / "runtime" / "pending-workstreams"
    p_dir.mkdir(parents=True, exist_ok=True)
    p_file = p_dir / project_id

    existing = set(pending_workstream_ids(project_id))
    existing.add(channel_id)

    rendered = "\n".join(sorted(existing)) + "\n"
    fd = os.open(p_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(rendered)


def forget_pending_workstream(project_id: str, channel_id: str) -> None:
    """Forget a channel id that successfully reconciled for a project."""
    if not is_knowledge_enabled():
        return

    p_file = _get_knowledge_root() / "runtime" / "pending-workstreams" / project_id
    if not p_file.is_file():
        return

    existing = [ch for ch in pending_workstream_ids(project_id) if ch != channel_id]
    if not existing:
        try:
            p_file.unlink()
        except OSError:
            pass
    else:
        rendered = "\n".join(sorted(existing)) + "\n"
        fd = os.open(p_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(rendered)


def quarantine_corpus(collection_id: str) -> None:
    """Quarantine corpora/{id} to quarantine/{id}-{utc} and clean up qmd/{id}."""
    root = _get_knowledge_root()
    corp_dir = root / "corpora" / collection_id
    if corp_dir.is_dir():
        utc_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        q_dir = root / "quarantine" / f"{collection_id}-{utc_ts}"
        q_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            corp_dir.rename(q_dir)
        except Exception as exc:
            logger.warning("quarantine rename failed: %s", type(exc).__name__)

    qmd_dir = root / "qmd" / collection_id
    if qmd_dir.is_dir():
        import shutil
        try:
            shutil.rmtree(qmd_dir, ignore_errors=True)
        except Exception:
            pass


def ingest_knowledge_note(
    collection_id: str,
    title: str,
    text: str,
    project: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Ingest a note into collection corpus. Returns dict with relpath and title."""
    if not is_knowledge_enabled():
        raise KnowledgeDisabledError("knowledge_disabled")

    import corpusnote
    corpus = _get_corpus_handle(collection_id)
    secrets = vault_extra_secrets()
    try:
        page = corpusnote.ingest_text(
            corpus,
            title=title,
            text=text,
            project=project,
            project_id=project_id,
            extra_secrets=secrets,
        )
        return {"success": True, "relpath": page.relpath, "title": page.frontmatter.get("title")}
    except corpusnote.RejectedWrite as exc:
        raise RejectedWrite(str(exc)) from exc


def propose_knowledge_rule(
    collection_id: str,
    slug: str,
    title: str,
    body: str,
    project: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Propose a rule into collection corpus. Returns dict with relpath and confirmed: False."""
    if not is_knowledge_enabled():
        raise KnowledgeDisabledError("knowledge_disabled")

    import corpusnote
    corpus = _get_corpus_handle(collection_id)
    secrets = vault_extra_secrets()
    try:
        page = corpusnote.propose_rule(
            corpus,
            slug=slug,
            title=title,
            body=body,
            project=project,
            project_id=project_id,
            extra_secrets=secrets,
        )
        return {"relpath": page.relpath, "confirmed": False}
    except corpusnote.RejectedWrite as exc:
        raise RejectedWrite(str(exc)) from exc


def confirm_knowledge_rule(
    collection_id: str,
    relpath: str,
    confirmed_by: str,
) -> dict[str, Any]:
    """Confirm a proposed rule in collection corpus. Returns dict with relpath and confirmed: True."""
    if not is_knowledge_enabled():
        raise KnowledgeDisabledError("knowledge_disabled")

    import corpusnote
    corpus = _get_corpus_handle(collection_id)
    try:
        page = corpusnote.confirm_rule(
            corpus,
            relpath,
            confirmed_by=confirmed_by,
        )
        return {"relpath": page.relpath, "confirmed": True}
    except corpusnote.RejectedWrite as exc:
        raise RejectedWrite(str(exc)) from exc
    except corpusnote.MissingPage as exc:
        raise MissingPage(str(exc)) from exc

