"""Autonomous Verify-and-Retry Loop (L1-L3) for RDC Supervised Sessions.

Implements harness-owned verification, structured failure feedback, positive pattern
steering ("The Line Ahead"), local dead-end recording, and the three honest loop exits:
SUCCESS, BUDGET, and STALL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db.repositories import get_project_repo
from .session_manager import Session, SessionStatus, get_session_manager

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROUNDS = 3
MAX_ALLOWED_ROUNDS = 10

_FAILURE_LINE_RE = re.compile(
    r"(?:^|\b)(?:FAIL|FAILED|ERROR|AssertionError|TypeError|ValueError|SyntaxError|"
    r"ImportError|ModuleNotFoundError|KeyError|AttributeError|RuntimeError|"
    r"error TS\d+|E\s{2,}|✖|×|expected\b.*?\bgot\b)",
    re.IGNORECASE,
)


def effective_session_path(session: Session) -> Path | None:
    """Return the session's isolated worktree path if valid, otherwise the project root."""
    wt_path = getattr(session, "worktree_path", None)
    if wt_path:
        p = Path(wt_path)
        if p.is_dir():
            return p.resolve()
    record = get_project_repo().get(session.project)
    if record and record.path:
        proj_p = Path(record.path)
        if proj_p.is_dir():
            return proj_p.resolve()
    return None


def resolve_verification_command(session: Session, explicit_command: str | None = None) -> str | None:
    """Resolve the verification command for a session from explicit arg, policy/metadata, or project profile."""
    if explicit_command and explicit_command.strip():
        return explicit_command.strip()

    policy = session.policy or {}
    metadata = session.metadata or {}
    for candidate in (
        policy.get("verification_command"),
        metadata.get("verification_command"),
        (metadata.get("loop") or {}).get("verification_command") if isinstance(metadata.get("loop"), dict) else None,
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()

    record = get_project_repo().get(session.project)
    if record:
        profile_dict = (record.config or {}).get("profile", {}) if isinstance(record.config, dict) else {}
        cmd = profile_dict.get("test_command")
        if cmd and str(cmd).strip():
            return str(cmd).strip()
        if hasattr(record, "profile") and record.profile and getattr(record.profile, "test_command", None):
            return str(record.profile.test_command).strip()

    return None


def resolve_max_rounds(session: Session, has_verification_command: bool) -> int:
    """Determine maximum verification rounds for a session."""
    metadata = session.metadata or {}
    budget = session.budget or {}
    policy = session.policy or {}
    loop_meta = metadata.get("loop") if isinstance(metadata.get("loop"), dict) else {}

    for raw in (
        loop_meta.get("max_rounds"),
        budget.get("max_rounds"),
        policy.get("max_rounds"),
        metadata.get("max_rounds"),
    ):
        if raw is not None:
            try:
                val = int(raw)
                return max(1, min(val, MAX_ALLOWED_ROUNDS))
            except (TypeError, ValueError):
                pass

    return DEFAULT_MAX_ROUNDS if has_verification_command else 1


_NODE_RUNNERS = {"npm", "pnpm", "yarn", "bun"}
_PY_RUNNERS = {"python", "python3", "pytest", "py.test", "uv"}
_SHELL_TOKENS = {"&&", "||", ";", "|", ">", "<"}
BLOCKED_LOOP_EXITS = frozenset({"stall", "budget", "runtime", "verification_error", "retry_error"})


def loop_acceptance_block(session: Session) -> str | None:
    """Reason a failed or skipped verification cannot be accepted without an override."""
    loop = (session.metadata or {}).get("loop") or {}
    if not isinstance(loop, dict):
        return None
    reason = loop.get("exit_reason")
    if reason in BLOCKED_LOOP_EXITS:
        return f"verification {reason}"
    status = loop.get("status")
    if status in {"stalled", "budget_exhausted", "unverified"}:
        return f"verification {status}"
    return None


def _normalize_command(text: str) -> str:
    return " ".join(text.split())


def verification_runtime_blocker(command: str, cwd: Path, project_root: Path | None = None) -> str | None:
    """Return why the harness should not run this command in the session directory."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return "Verification command is not valid, so the check was not run."
    if not argv or any(token in _SHELL_TOKENS for token in argv):
        return "Verification must be a single command, so the check was not run."
    exe = argv[0]
    base = Path(exe).name
    if base in _NODE_RUNNERS and (cwd / "package.json").is_file() and not (cwd / "node_modules").is_dir():
        return "The session directory has package.json and no node_modules, so the verification command was not run."
    if base in _PY_RUNNERS and project_root is not None:
        try:
            same_tree = project_root.resolve() == cwd.resolve()
        except OSError:
            same_tree = False
        if not same_tree:
            for venv_name in (".venv", "venv"):
                if (project_root / venv_name).is_dir() and not (cwd / venv_name).is_dir():
                    return f"The session directory has no {venv_name}, so the verification command was not run."
    exe_path = Path(exe)
    looks_like_path = exe_path.is_absolute() or "/" in exe or exe.startswith(".")
    if looks_like_path:
        candidate = exe_path if exe_path.is_absolute() else cwd / exe_path
        if not candidate.is_file():
            return "Verification command was not found in the session directory, so the check was not run."
    elif shutil.which(exe) is None:
        return "Verification command was not found, so the check was not run."
    return None


def _scrub_verification_text(text: str) -> str:
    """Pattern-scrub verification output and remove vault secrets before it is stored."""
    from .scrubber import SecretScrubber

    scrubber = SecretScrubber()
    try:
        from .knowledge_bridge import vault_extra_secrets

        for secret in vault_extra_secrets():
            scrubber.add_known_secret(secret)
    except Exception as exc:
        logger.debug("verification scrub skipped vault secrets: %s", type(exc).__name__)
    return scrubber.scrub(text or "")


def summarize_verification_output(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> tuple[str, str]:
    """Extract a concise failure report and a normalized failure signature for stall detection.

    Returns (concise_feedback, failure_signature).
    """
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part.strip())
    lines = [line.rstrip() for line in combined.splitlines() if line.strip()]

    if exit_code == 0:
        tail = lines[-3:] if lines else ["ok"]
        return f"PASS ({command}): " + " | ".join(tail)[:300], "pass"

    matched_lines: list[str] = []
    for line in lines:
        if _FAILURE_LINE_RE.search(line):
            clean = line.strip()[:220]
            if clean not in matched_lines:
                matched_lines.append(clean)
            if len(matched_lines) >= 15:
                break

    if not matched_lines:
        matched_lines = [line.strip()[:220] for line in lines[-12:]]

    # Normalize numbers/timestamps/addresses out of signature so identical failures match reliably
    norm_basis = "\n".join(
        re.sub(r"\b0x[0-9a-fA-F]+\b|\b\d+\.\d+s\b", "", ln.lower())
        for ln in matched_lines[:10]
    )
    signature = "sig-" + hashlib.sha256(f"{exit_code}:{norm_basis}".encode("utf-8")).hexdigest()[:12]

    snippet = "\n".join(f"  {ln}" for ln in matched_lines[:12])
    feedback = f"FAILED `{command}` (exit {exit_code}):\n{snippet}"
    return feedback[:1800], signature


def capture_git_evidence_if_changed(session: Session, cwd: Path, source: str = "rdc") -> dict[str, Any] | None:
    """Inspect the session's working tree and record Git evidence if changes exist."""
    if not (cwd / ".git").exists():
        return None

    def _git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        return res.stdout.strip() if res.returncode == 0 else ""

    head = _git("rev-parse", "HEAD")
    status_short = _git("status", "--short")
    changed_files = [line for line in _git("diff", "--name-only", "HEAD").splitlines() if line]
    if not changed_files and status_short:
        for line in status_short.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                changed_files.append(parts[1].strip())
    diff_stat = _git("diff", "--stat", "HEAD")

    baseline_head = ((session.metadata or {}).get("git_baseline") or {}).get("head", "")
    has_changes = bool(status_short or changed_files or (baseline_head and head and baseline_head != head))
    if not has_changes:
        return None

    data = {
        "head": head,
        "status": status_short,
        "changed_files": changed_files,
        "diff_stat": diff_stat,
    }
    evidence_id = secrets.token_hex(16)
    db = get_session_manager()._db()
    db.execute(
        """INSERT INTO session_evidence
           (id, session_id, evidence_type, title, data, source)
           VALUES (?, ?, 'git', 'Git working tree', ?, ?)""",
        (evidence_id, session.id, json.dumps(data), source),
    )
    db.commit()
    return {"id": evidence_id, "evidence_type": "git", "data": data}


def run_verification_sync(
    session: Session,
    command: str | None = None,
    *,
    source: str = "rdc",
    timeout: int = 900,
) -> dict[str, Any]:
    """Run the session's verification command in its effective worktree/project directory and record evidence."""
    resolved_cmd = resolve_verification_command(session, command)
    if not resolved_cmd:
        raise ValueError("No verification command configured")

    cwd = effective_session_path(session)
    if not cwd:
        raise LookupError(f"Project path not found for session {session.id}")

    try:
        argv = shlex.split(resolved_cmd)
    except ValueError as exc:
        raise ValueError(f"Invalid verification command: {exc}") from exc

    if not argv or any(token in {"&&", "||", ";", "|", ">", "<"} for token in argv):
        raise ValueError("Verification must be a single executable command")

    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"Verification command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Verification timed out after 15 minutes") from exc

    duration = round(time.monotonic() - started, 3)
    raw_stdout = result.stdout[-30000:]
    raw_stderr = result.stderr[-15000:]
    concise_feedback, signature = summarize_verification_output(
        resolved_cmd,
        result.returncode,
        raw_stdout,
        raw_stderr,
    )
    stdout_tail = _scrub_verification_text(raw_stdout)
    stderr_tail = _scrub_verification_text(raw_stderr)
    concise_feedback = _scrub_verification_text(concise_feedback)

    data = {
        "command": resolved_cmd,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "duration_seconds": duration,
        "stdout": stdout_tail,
        "stderr": stderr_tail,
        "concise_feedback": concise_feedback,
        "signature": signature,
    }
    evidence_id = secrets.token_hex(16)
    db = get_session_manager()._db()
    db.execute(
        """INSERT INTO session_evidence
           (id, session_id, evidence_type, title, data, source)
           VALUES (?, ?, 'verification', 'Verification command', ?, ?)""",
        (evidence_id, session.id, json.dumps(data), source),
    )
    if result.returncode == 0:
        now = datetime.now().isoformat()
        rows = db.execute(
            """SELECT criterion_index, criterion FROM session_criteria
               WHERE session_id = ? AND status = 'unevaluated'""",
            (session.id,),
        ).fetchall()
        normalized_cmd = _normalize_command(resolved_cmd)
        for row in rows:
            if _normalize_command(row["criterion"]) != normalized_cmd:
                continue
            db.execute(
                """UPDATE session_criteria
                   SET status = 'passed', note = 'Automatically satisfied by passing RDC verification',
                       evidence_ids = ?, evaluated_by = 'rdc', evaluated_at = ?
                   WHERE session_id = ? AND criterion_index = ? AND status = 'unevaluated'""",
                (json.dumps([evidence_id]), now, session.id, row["criterion_index"]),
            )
    db.commit()

    # Also snapshot git working tree evidence when verification runs
    try:
        capture_git_evidence_if_changed(session, cwd, source=source)
    except Exception:
        logger.debug("git evidence snapshot skipped for %s", session.id, exc_info=True)

    return {"id": evidence_id, "evidence_type": "verification", "data": data}


def _fetch_proven_patterns_hint(session: Session) -> str:
    """Return a concise block of Proven Patterns ('The Line Ahead') for retry steering."""
    try:
        from .knowledge_bridge import (
            corpus_id_for_pack,
            get_knowledge_digest,
            is_knowledge_enabled,
        )

        if not is_knowledge_enabled():
            return ""
        corpus_id = corpus_id_for_pack(session.project, session.channel_id)
        if not corpus_id:
            return ""
        digest = get_knowledge_digest(corpus_id, project=session.project, include_commons=True)
        patterns = digest.get("patterns") or []
        if not patterns:
            return ""
        lines = ["Proven Patterns to follow (The Line Ahead):"]
        for pat in patterns[:4]:
            lines.append(f"- [{pat.get('topic')}] {pat.get('statement')}")
        return "\n".join(lines)
    except Exception:
        return ""


async def handle_agent_turn_completed(
    session: Session,
    summary: str,
    *,
    agent_exit_code: int | None = 0,
) -> dict[str, Any]:
    """Evaluate a completed agent turn: run verification if configured, and either finish or loop.

    Returns a dict with:
      - action: "finish" | "retry"
      - end_status: SessionStatus (when action == "finish")
      - summary: updated summary text
      - loop: updated loop metadata dict
    """
    sm = get_session_manager()
    metadata = dict(session.metadata or {})
    verif_cmd = resolve_verification_command(session)
    max_rounds = resolve_max_rounds(session, has_verification_command=bool(verif_cmd))

    loop_state: dict[str, Any] = dict(metadata.get("loop") or {})
    current_round = int(loop_state.get("round") or 1)
    history: list[dict[str, Any]] = list(loop_state.get("history") or [])

    # If the agent process itself crashed with non-zero exit code and no verification command exists
    if not verif_cmd:
        end_status = sm.successful_execution_status(session) if (agent_exit_code in (0, None)) else SessionStatus.FAILED
        loop_state.update({
            "round": current_round,
            "max_rounds": max_rounds,
            "status": "completed" if end_status != SessionStatus.FAILED else "failed",
            "exit_reason": "no_check" if end_status != SessionStatus.FAILED else "agent_error",
        })
        metadata["loop"] = loop_state
        session.metadata = metadata
        sm._persist(session)
        return {
            "action": "finish",
            "end_status": end_status,
            "summary": summary,
            "loop": loop_state,
        }

    cwd = effective_session_path(session)
    project_record = get_project_repo().get(session.project)
    project_root = Path(project_record.path) if project_record and project_record.path else None
    runtime_block = (
        "The session directory is missing, so the verification command was not run."
        if cwd is None
        else verification_runtime_blocker(verif_cmd, cwd, project_root)
    )
    if runtime_block:
        end_status = sm.successful_execution_status(session) if agent_exit_code in (0, None) else SessionStatus.FAILED
        loop_state.update({
            "round": current_round,
            "max_rounds": max_rounds,
            "status": "unverified",
            "exit_reason": "runtime",
            "verification_command": verif_cmd,
            "last_summary": runtime_block[:300],
        })
        metadata["loop"] = loop_state
        session.metadata = metadata
        if end_status != SessionStatus.FAILED:
            session.blocker = runtime_block[:240]
        sm._persist(session)
        sm.log_event(session, "session.loop_unverified", {"reason": "runtime"})
        return {
            "action": "finish",
            "end_status": end_status,
            "summary": summary,
            "loop": loop_state,
        }

    # Run harness-owned verification in the session's worktree
    try:
        verif_res = await asyncio.to_thread(
            run_verification_sync,
            session,
            verif_cmd,
            source="rdc-loop",
        )
    except Exception as exc:
        logger.warning(
            "Auto-verification could not run for session %s: %s",
            session.id,
            type(exc).__name__,
        )
        end_status = sm.successful_execution_status(session) if (agent_exit_code in (0, None)) else SessionStatus.FAILED
        reason = "Verification could not be run."
        loop_state.update({
            "round": current_round,
            "max_rounds": max_rounds,
            "status": "unverified",
            "exit_reason": "verification_error",
            "last_error": type(exc).__name__,
            "last_summary": reason,
        })
        metadata["loop"] = loop_state
        session.metadata = metadata
        if end_status != SessionStatus.FAILED:
            session.blocker = reason
        sm._persist(session)
        return {
            "action": "finish",
            "end_status": end_status,
            "summary": summary,
            "loop": loop_state,
        }

    v_data = verif_res["data"]
    v_exit = int(v_data["exit_code"])
    concise_feedback = str(v_data.get("concise_feedback") or "")
    signature = str(v_data.get("signature") or "")

    round_entry = {
        "round": current_round,
        "exit_code": v_exit,
        "signature": signature,
        "duration_seconds": v_data.get("duration_seconds"),
        "summary": concise_feedback[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    history.append(round_entry)

    # 1. SUCCESS EXIT: Verification passed!
    if v_exit == 0:
        loop_state.update({
            "round": current_round,
            "max_rounds": max_rounds,
            "status": "passed",
            "exit_reason": "success",
            "verification_command": verif_cmd,
            "last_exit_code": 0,
            "last_summary": concise_feedback[:300],
            "history": history[-10:],
        })
        metadata["loop"] = loop_state
        session.metadata = metadata
        session.blocker = None
        badge = f"✓ Verified (`{verif_cmd}` passed on round {current_round}/{max_rounds})"
        enriched_summary = f"{summary.strip()}\n\n{badge}".strip() if summary else badge
        sm._persist(session)
        sm.log_event(
            session,
            "session.loop_verified",
            {"round": current_round, "max_rounds": max_rounds, "command": verif_cmd},
        )
        return {
            "action": "finish",
            "end_status": sm.successful_execution_status(session),
            "summary": enriched_summary,
            "loop": loop_state,
        }

    # 2. STALL EXIT: Same failure signature two rounds in a row
    prev_signatures = [h.get("signature") for h in history[:-1] if h.get("signature")]
    is_stalled = bool(signature and prev_signatures and prev_signatures[-1] == signature)

    if is_stalled or current_round >= max_rounds:
        exit_reason = "stall" if is_stalled else "budget"
        reason_label = (
            f"Stalled on round {current_round}/{max_rounds} (identical verification failure repeated)"
            if is_stalled
            else f"Verification failed after {current_round}/{max_rounds} rounds"
        )
        loop_state.update({
            "round": current_round,
            "max_rounds": max_rounds,
            "status": "stalled" if is_stalled else "budget_exhausted",
            "exit_reason": exit_reason,
            "verification_command": verif_cmd,
            "last_exit_code": v_exit,
            "last_summary": concise_feedback[:500],
            "history": history[-10:],
        })
        metadata["loop"] = loop_state
        session.metadata = metadata
        session.blocker = _scrub_verification_text(f"{reason_label}. The verification command exited {v_exit}.")
        enriched_summary = (
            f"{summary.strip()}\n\n⚠️ {reason_label}:\n{concise_feedback[:600]}"
            if summary
            else f"⚠️ {reason_label}:\n{concise_feedback[:600]}"
        ).strip()
        sm._persist(session)
        sm.log_event(
            session,
            f"session.loop_{exit_reason}",
            {"round": current_round, "max_rounds": max_rounds, "exit_code": v_exit},
        )
        return {
            "action": "finish",
            "end_status": sm.successful_execution_status(session),
            "summary": enriched_summary,
            "loop": loop_state,
        }

    # 3. RETRY: Advance to round N+1 with structured feedback + Proven Patterns
    next_round = current_round + 1
    loop_state.update({
        "round": next_round,
        "max_rounds": max_rounds,
        "status": "retrying",
        "exit_reason": None,
        "verification_command": verif_cmd,
        "last_exit_code": v_exit,
        "last_summary": concise_feedback[:500],
        "history": history[-10:],
    })
    metadata["loop"] = loop_state
    session.metadata = metadata
    session.output_summary = summary
    sm._persist(session)

    patterns_hint = await asyncio.to_thread(_fetch_proven_patterns_hint, session)
    feedback_parts = [
        f"[RDC Verify Loop — Round {next_round}/{max_rounds}]",
        f"Your previous attempt did not pass the harness verification check (`{verif_cmd}`).",
        "",
        concise_feedback,
        "",
        "DEAD END: Do not repeat the exact same change without addressing the failing assertion above.",
    ]
    if patterns_hint:
        feedback_parts.extend(["", patterns_hint])
    feedback_parts.extend([
        "",
        f"Inspect the failure, fix the root cause in the code (do not weaken or delete existing tests), and run `{verif_cmd}` to confirm it passes.",
    ])
    feedback_instruction = "\n".join(feedback_parts)

    sm.log_event(
        session,
        "session.loop_retry",
        {"from_round": current_round, "to_round": next_round, "max_rounds": max_rounds, "exit_code": v_exit},
    )

    # Transition temporarily to VERIFYING so instruct_session can cleanly claim and restart it
    session.status = SessionStatus.VERIFYING
    sm._persist(session)

    from .session_controls import instruct_session

    try:
        await asyncio.to_thread(instruct_session, session.id, feedback_instruction)
        return {
            "action": "retry",
            "round": next_round,
            "max_rounds": max_rounds,
            "loop": loop_state,
        }
    except Exception as exc:
        logger.warning(
            "Loop retry failed to start round %s for %s: %s",
            next_round,
            session.id,
            type(exc).__name__,
        )
        loop_state["status"] = "unverified"
        loop_state["exit_reason"] = "retry_error"
        loop_state["last_summary"] = "The next verification round could not be started."
        metadata["loop"] = loop_state
        session.metadata = metadata
        session.blocker = "The next verification round could not be started."
        sm._persist(session)
        return {
            "action": "finish",
            "end_status": sm.successful_execution_status(session),
            "summary": summary,
            "loop": loop_state,
        }
