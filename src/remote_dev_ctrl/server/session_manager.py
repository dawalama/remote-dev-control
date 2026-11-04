"""Agent Session Manager — orchestrates mission execution with event-based tracking.

A session wraps a mission — it can have zero or more terminals, and logs all
activity as structured events. The session event log is the unified timeline
of everything that happened, regardless of execution path (API-direct, terminal,
sub-agent).

Architecture:
    Session
    ├── Event log (unified timeline, persisted in events table via mission_id)
    │   ├── session.started
    │   ├── session.terminal_linked (terminal_id, role)
    │   ├── session.tool_executed (command, result)
    │   ├── session.agent_output (terminal output samples)
    │   ├── session.waiting_for_input
    │   ├── session.error
    │   └── session.completed / session.failed
    ├── Terminals: [term-abc, term-def] (zero or more, via session_terminals)
    ├── Log files: ~/.rdc/sessions/{session_id}.log per terminal
    └── Output summary (generated on completion)
"""

import asyncio
import enum
import json
import logging
import os
import re
import secrets
import shlex
import subprocess
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class SessionStatus(str, enum.Enum):
    PROPOSED = "proposed"
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def active_values(cls) -> tuple[str, ...]:
        return (cls.PENDING.value, cls.RUNNING.value, cls.WAITING.value, cls.VERIFYING.value)

    @classmethod
    def terminal_values(cls) -> tuple[str, ...]:
        return (cls.DONE.value, cls.COMPLETED.value, cls.FAILED.value, cls.CANCELLED.value)

    @classmethod
    def staged_values(cls) -> tuple[str, ...]:
        return (cls.PROPOSED.value,)


class AttentionState(str, enum.Enum):
    NEEDS_ATTENTION = "needs_attention"
    RUNNING = "running"
    STAGED = "staged"
    ACCEPTED = "accepted"
    CLOSED = "closed"


def get_attention_state(status: SessionStatus | str) -> AttentionState:
    """Map internal SessionStatus to high-level AttentionState."""
    val = status.value if isinstance(status, SessionStatus) else str(status)
    if val in (SessionStatus.WAITING.value, SessionStatus.VERIFYING.value, SessionStatus.FAILED.value):
        return AttentionState.NEEDS_ATTENTION
    if val in (SessionStatus.PENDING.value, SessionStatus.RUNNING.value):
        return AttentionState.RUNNING
    if val == SessionStatus.PROPOSED.value:
        return AttentionState.STAGED
    if val == SessionStatus.COMPLETED.value:
        return AttentionState.ACCEPTED
    return AttentionState.CLOSED

from .scrubber import SecretScrubber
from .utils import (
    strip_ansi, safe_json_loads, json_field, enum_value,
    get_rdc_db, get_channel_manager, get_terminal_manager,
    get_state_machine, get_project_repo, get_rdc_home,
)

logger = logging.getLogger(__name__)

# Outbound approval text. Pattern scrub only — do not load the vault here.
_outbound_scrubber = SecretScrubber()
WAITING_REASON = "Agent is waiting for input"


from .agent_registry import (
    build_agent_command as _registry_build_agent_command,
    check_provider_blocker as _registry_check_provider_blocker,
    discover_agent_aliases,
    list_provider_readiness as _registry_list_provider_readiness,
    resolve_codex_model_flag as _resolve_codex_model_flag,
    resolve_provider_spec as _registry_resolve_provider_spec,
    wrap_alias_invocation as _wrap_alias_invocation,
)


def resolve_provider_spec(provider: str) -> tuple[str, str, dict[str, str] | None]:
    """Return (driver_family, invocation_prefix, alias_info) for a provider or alias."""
    driver, invocation, alias_info, _spec = _registry_resolve_provider_spec(provider)
    return driver, invocation, alias_info


def build_agent_command(
    provider: str,
    description: str,
    *,
    resume: bool = False,
    fallback_description: str | None = None,
) -> str:
    """Build the terminal command for the provider, alias, or custom wrapper."""
    return _registry_build_agent_command(
        provider,
        description,
        resume=resume,
        fallback_description=fallback_description,
    )


def agent_provider_blocker(provider: str) -> str | None:
    """Return a user-facing blocker before creating a terminal for a provider or alias."""
    return _registry_check_provider_blocker(
        provider,
        which_fn=shutil.which,
        run_fn=subprocess.run,
    )


def list_agent_provider_readiness() -> list[dict[str, Any]]:
    return _registry_list_provider_readiness(
        which_fn=shutil.which,
        blocker_fn=agent_provider_blocker,
    )

# Patterns that indicate the agent is waiting for user input
_INPUT_PATTERNS = [
    re.compile(r"\?\s+.*\(y/n\)", re.IGNORECASE),
    re.compile(r"\?\s+.*\[Y/n\]", re.IGNORECASE),
    re.compile(r"\?\s+.*\[yes/no\]", re.IGNORECASE),
    re.compile(r"Do you want to (proceed|continue|approve)", re.IGNORECASE),
    re.compile(r"Press (Enter|any key) to continue", re.IGNORECASE),
    re.compile(r"Approve\?", re.IGNORECASE),
    re.compile(r"\? Select", re.IGNORECASE),
    re.compile(r"Enter .* to continue", re.IGNORECASE),
]

# Shell prompt — indicates agent command has exited
_SHELL_PROMPT_RE = re.compile(r"[$%>]\s*$")

# Exit signal from trap wrapper — instant completion detection
_EXIT_SIGNAL_RE = re.compile(r"__RDC_EXIT:(\d+)")


def extract_agent_report(transcript: str, provider: str | None) -> str:
    """Extract the provider's final human-readable answer from a terminal transcript."""
    cleaned = strip_ansi(transcript).replace("\r\n", "\n")
    final_match = re.search(r"\n__RDC_FINAL_BEGIN__\n(.*?)\n__RDC_FINAL_END__", cleaned, re.DOTALL)
    if final_match:
        final_text = final_match.group(1).strip()
        if final_text:
            return final_text
    # Codex prints this stable role marker even when RDC recorded the provider
    # as "orchestrated". Inspect the transcript instead of trusting that alias.
    marker = "codex\n"
    marker_index = cleaned.rfind(marker)
    if marker_index >= 0:
        report = cleaned[marker_index + len(marker):]
        report = re.split(r"\ntokens used\n|\n__RDC_FINAL_BEGIN__|\n__RDC_EXIT:\d+", report, maxsplit=1)[0]
        if report.strip():
            return report.strip()
    error_lines = [
        line.strip()
        for line in cleaned.splitlines()
        if line.strip().lower().startswith(("error:", "fatal:"))
    ]
    if error_lines:
        deduped = list(dict.fromkeys(error_lines))
        return _outbound_scrubber.scrub("\n".join(deduped[-3:]))
    return ""


@dataclass
class Session:
    id: str
    channel_id: str
    project: str
    terminal_ids: list[str] = field(default_factory=list)
    task_id: Optional[str] = None
    description: str = ""
    status: SessionStatus = SessionStatus.PENDING
    agent_provider: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    output_summary: Optional[str] = None
    metadata: Optional[dict] = None
    goal: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    control_owner: str = "none"
    current_step: Optional[str] = None
    blocker: Optional[str] = None
    policy: dict = field(default_factory=dict)
    budget: Optional[dict] = None
    unresolved_risks: list[str] = field(default_factory=list)


class SessionManager:
    """Manages agent sessions — creation, monitoring, event logging, and lifecycle."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._monitors: dict[str, asyncio.Task] = {}
        self._last_output_pos: dict[str, int] = {}  # terminal_id → last read position

    def _db(self):
        return get_rdc_db()

    @staticmethod
    def successful_execution_status(session: Session) -> SessionStatus:
        return SessionStatus.VERIFYING if (session.metadata or {}).get("supervised") else SessionStatus.DONE

    # ── Event Logging ──

    def log_event(self, session: Session, event_type: str, data: Optional[dict] = None):
        """Log a structured event to the session's timeline."""
        try:
            cm = get_channel_manager()
            cm.emit_event(
                event_type,
                channel_id=session.channel_id,
                project_id=session.project,
                mission_id=session.id,  # session_id stored in mission_id column
                data=data or {},
            )
        except Exception:
            logger.debug("Failed to log session event", exc_info=True)

    def get_events(self, session_id: str, limit: int = 100) -> list[dict]:
        """Get events for a session, ordered chronologically."""
        db = self._db()
        rows = db.execute(
            "SELECT * FROM events WHERE mission_id = ? ORDER BY timestamp ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"], "timestamp": r["timestamp"], "type": r["type"],
                "data": json.loads(r["data"]) if r["data"] else {},
            }
            for r in rows
        ]

    def request_approval(self, session: Session, summary: str, terminal_id: str) -> str:
        """Create one durable pending approval for a detected terminal prompt."""
        db = self._db()
        existing = db.execute(
            "SELECT id FROM session_approvals WHERE session_id = ? AND status = 'pending' LIMIT 1",
            (session.id,),
        ).fetchone()
        if existing:
            return existing["id"]
        safe_summary = _outbound_scrubber.scrub(summary or "").strip() or WAITING_REASON
        approval_id = f"approval-{secrets.token_hex(8)}"
        db.execute(
            """INSERT INTO session_approvals
               (id, session_id, action_type, risk_level, summary, details, requested_by)
               VALUES (?, ?, 'terminal_input', 'medium', ?, ?, ?)""",
            (approval_id, session.id, safe_summary[-1000:], json.dumps({"terminal_id": terminal_id}), "agent"),
        )
        db.commit()
        self.log_event(session, "session.approval_requested", {"approval_id": approval_id})
        try:
            from .session_notifier import notify_session_event

            # Same event key as waiting status so lifecycle + approval do not double-notify.
            notify_session_event(
                session,
                event="needs_you",
                reason=safe_summary[-240:],
                extra_key="",
            )
        except Exception:
            logger.debug("approval notify failed", exc_info=True)
        return approval_id

    # ── CRUD ──

    def create_session(
        self,
        channel_id: str,
        project: str,
        description: str,
        agent_provider: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        acceptance_criteria: Optional[list[str]] = None,
        policy: Optional[dict] = None,
        budget: Optional[dict] = None,
        status: Optional[SessionStatus] = None,
    ) -> Session:
        """Create a new session and persist to DB."""
        session = Session(
            id=f"sess-{secrets.token_hex(6)}",
            channel_id=channel_id,
            project=project,
            description=description,
            status=status or SessionStatus.PENDING,
            agent_provider=agent_provider,
            task_id=task_id,
            metadata=metadata,
            goal=description,
            acceptance_criteria=acceptance_criteria or [],
            policy=policy or {},
            budget=budget,
        )
        self._sessions[session.id] = session
        self._persist(session)
        if session.acceptance_criteria:
            db = self._db()
            db.executemany(
                """INSERT INTO session_criteria
                   (session_id, criterion_index, criterion) VALUES (?, ?, ?)""",
                [(session.id, index, criterion) for index, criterion in enumerate(session.acceptance_criteria)],
            )
            db.commit()
        self.log_event(session, "session.created", {"description": description[:200], "project": project})
        return session

    def get(self, session_id: str) -> Optional[Session]:
        if session_id in self._sessions:
            return self._sessions[session_id]
        return self._load(session_id)

    def list_for_channel(self, channel_id: str) -> list[Session]:
        db = self._db()
        rows = db.execute(
            "SELECT * FROM sessions WHERE channel_id = ? ORDER BY created_at DESC",
            (channel_id,),
        ).fetchall()
        return self._rows_to_sessions(rows)

    def list_all(self, limit: int = 200) -> list[Session]:
        rows = self._db().execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return self._rows_to_sessions(rows)

    def resolve_terminal_approvals(self, session_id: str, decided_by: str = "terminal") -> None:
        """Close terminal prompts that the session has left behind.

        A person can answer in the terminal without using the approval button.
        The pending row must not keep winning the brief, and it must not block
        the next real prompt.
        """
        db = self._db()
        db.execute(
            """UPDATE session_approvals
               SET status = 'resolved', decided_by = ?, decided_at = ?
               WHERE session_id = ? AND status = 'pending' AND action_type = 'terminal_input'""",
            (decided_by, datetime.now().isoformat(), session_id),
        )
        db.commit()

    def claim_for_start(self, session_id: str, *, continue_review: bool = False) -> Optional[str]:
        """Move a startable session to running.

        Returns the previous status, or None when another start already claimed it.
        Review states are included only when a follow-up is continuing that session.
        """
        from .db.connection import get_db_manager

        startable = ["proposed", "pending", "failed", "cancelled"]
        if continue_review:
            startable.extend(("verifying", "completed", "done"))
        db = self._db()
        lock = get_db_manager().get_lock("rdc")
        now = datetime.now()
        with lock:
            row = db.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not row or row["status"] not in set(startable):
                return None
            previous = row["status"]
            cur = db.execute(
                """UPDATE sessions
                   SET status = ?, updated_at = ?, completed_at = NULL, blocker = NULL
                   WHERE id = ? AND status = ?""",
                ("running", now.isoformat(), session_id, previous),
            )
            db.commit()
            if cur.rowcount != 1:
                return None
            session = self._sessions.get(session_id)
            if session is not None:
                session.status = SessionStatus.RUNNING
                session.updated_at = now
                session.completed_at = None
                session.blocker = None
            return previous

    def _note_waiting_for_input(self, session: Session, terminal_id: str, cm) -> None:
        """Record that the agent needs a person. The terminal keeps the prompt."""
        session.blocker = WAITING_REASON
        self._persist(session)
        self.update_status(session.id, SessionStatus.WAITING, notify_reason=WAITING_REASON)
        self.log_event(session, "session.waiting_for_input", {"terminal_id": terminal_id})
        self.request_approval(session, WAITING_REASON, terminal_id)
        cm.post_message(
            session.channel_id,
            role="system",
            content=WAITING_REASON,
            metadata={
                "type": "a2ui",
                "components": [
                    {
                        "type": "confirm",
                        "title": "The agent needs your input",
                        "description": "Open the terminal to respond.",
                        "confirm_label": "Open Terminal",
                        "cancel_label": "Dismiss",
                    }
                ],
            },
        )

    def list_active(self) -> list[Session]:
        db = self._db()
        placeholders = ",".join("?" for _ in SessionStatus.active_values())
        rows = db.execute(
            f"SELECT * FROM sessions WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            SessionStatus.active_values(),
        ).fetchall()
        return self._rows_to_sessions(rows)

    def update_status(
        self,
        session_id: str,
        status: SessionStatus | str,
        output_summary: Optional[str] = None,
        notify_reason: Optional[str] = None,
    ):
        session = self.get(session_id)
        if not session:
            return
        session.status = SessionStatus(status) if isinstance(status, str) else status
        session.updated_at = datetime.now()
        if session.status in (SessionStatus.DONE, SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED):
            session.completed_at = datetime.now()
            session.control_owner = "none"
        if output_summary:
            session.output_summary = output_summary
        self._persist(session)
        if session.status != SessionStatus.WAITING:
            self.resolve_terminal_approvals(session.id)
        self.log_event(session, f"session.{session.status.value}", {"summary": output_summary[:200] if output_summary else None})
        # Central lifecycle notify (channel + Telegram). Dedupe inside notifier.
        # Callers pass notify_reason when the user-facing line is not the summary.
        try:
            from .session_notifier import notify_status_transition

            reason = notify_reason if notify_reason is not None else (output_summary or "")
            notify_status_transition(session, session.status, reason=reason)
        except Exception:
            logger.debug("session notify failed", exc_info=True)

    # ── Terminal Management ──

    def link_terminal(self, session_id: str, terminal_id: str, role: str = "agent"):
        """Link a terminal to a session and start monitoring it."""
        session = self.get(session_id)
        if not session:
            return
        if terminal_id not in session.terminal_ids:
            session.terminal_ids.append(terminal_id)
        if session.status == SessionStatus.PENDING:
            session.status = SessionStatus.RUNNING
        session.control_owner = "agent"
        session.updated_at = datetime.now()
        self._persist(session)

        # Persist to junction table
        db = self._db()
        db.execute(
            "INSERT OR IGNORE INTO session_terminals (session_id, terminal_id, role) VALUES (?, ?, ?)",
            (session_id, terminal_id, role),
        )
        db.commit()

        # Link terminal to channel
        try:
            cm = get_channel_manager()
            cm.link_terminal(terminal_id, session.channel_id)
        except Exception:
            pass

        # Broadcast state
        try:
            sm = get_state_machine()
            sm._broadcast_state_sync()
        except Exception:
            pass

        self.log_event(session, "session.terminal_linked", {"terminal_id": terminal_id, "role": role})

        # Start monitor for this terminal
        self._start_terminal_monitor(session, terminal_id)

    def get_terminal_ids(self, session_id: str) -> list[str]:
        """Get all terminal IDs for a session."""
        db = self._db()
        rows = db.execute(
            "SELECT terminal_id FROM session_terminals WHERE session_id = ? ORDER BY linked_at",
            (session_id,),
        ).fetchall()
        return [r["terminal_id"] for r in rows]

    # ── Log Tool Execution (for API-direct work) ──

    def log_tool_call(self, session_id: str, tool_name: str, params: dict, result: Any):
        """Log an orchestrator tool call to the session timeline."""
        session = self.get(session_id)
        if not session:
            return
        self.log_event(session, "session.tool_executed", {
            "tool": tool_name,
            "params": {k: str(v)[:100] for k, v in params.items()},
            "success": result.get("success") if isinstance(result, dict) else True,
        })

    # ── Terminal Output Monitors ──

    def _start_terminal_monitor(self, session: Session, terminal_id: str):
        monitor_key = f"{session.id}:{terminal_id}"
        if monitor_key in self._monitors:
            return

        async def _monitor():
            try:
                await self._monitor_terminal(session, terminal_id)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Terminal monitor failed: %s/%s", session.id, terminal_id)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No running event loop for terminal monitor %s", monitor_key)
            return
        self._monitors[monitor_key] = loop.create_task(_monitor())

    async def _monitor_terminal(self, session: Session, terminal_id: str):
        """Monitor a single terminal's output for a session."""
        tm = get_terminal_manager()
        cm = get_channel_manager()
        pos_key = terminal_id
        self._last_output_pos[pos_key] = 0
        idle_count = 0
        output_seen = False  # Don't check completion until agent has produced output
        last_event_type: Optional[str] = None

        while session.status in (SessionStatus.RUNNING, SessionStatus.WAITING):
            await asyncio.sleep(3)

            # Refresh session from memory (might have been updated by another monitor)
            session = self.get(session.id) or session

            term = tm.get(terminal_id)
            if not term:
                # Terminal gone
                self.log_event(session, "session.terminal_closed", {"terminal_id": terminal_id})
                # If this was the last active terminal, check if session should complete
                active_terms = [tid for tid in session.terminal_ids if tm.get(tid)]
                if not active_terms and session.status in (SessionStatus.RUNNING, SessionStatus.WAITING):
                    summary = await self._generate_summary(session)
                    end_status = self.successful_execution_status(session) if session.status == SessionStatus.RUNNING else SessionStatus.FAILED
                    self.update_status(session.id, end_status, output_summary=summary)
                    components: list[dict] = [
                        {"type": "task_card", "title": session.description[:80], "status": end_status.value, "project": session.project},
                    ]
                    if summary:
                        components.append({"type": "text", "content": summary})
                    cm.post_message(session.channel_id, role="system", content="Session completed." if end_status != SessionStatus.FAILED else "Session ended — terminal closed.",
                        metadata={"type": "a2ui", "components": components})
                break

            # Check terminal status
            term_status = term.status.value if hasattr(term.status, "value") else str(term.status)
            if term_status in ("stopped", "error"):
                self.log_event(session, "session.terminal_stopped", {"terminal_id": terminal_id, "status": term_status})
                active_terms = [tid for tid in session.terminal_ids if tm.get(tid) and tid != terminal_id]
                if not active_terms and session.status in (SessionStatus.RUNNING, SessionStatus.WAITING):
                    terminal_text = strip_ansi(bytes(term._output_buffer).decode("utf-8", errors="replace"))
                    exit_match = _EXIT_SIGNAL_RE.search(terminal_text)
                    exit_code = int(exit_match.group(1)) if exit_match else None
                    summary = await self._generate_summary(session)
                    end_status = self.successful_execution_status(session) if exit_code == 0 else SessionStatus.FAILED
                    self.update_status(session.id, end_status, output_summary=summary)
                    cm.post_message(session.channel_id, role="system", content=f"Session ended — terminal {term_status}.",
                        metadata={"type": "a2ui", "components": [
                            {"type": "task_card", "title": session.description[:80], "status": end_status.value, "project": session.project},
                        ]})
                break

            # Read new output
            try:
                buf = bytes(term._output_buffer)
                last_pos = self._last_output_pos.get(pos_key, 0)

                if len(buf) <= last_pos:
                    idle_count += 1

                    # Agent exited — shell prompt returned after idle (only after we've seen output)
                    if idle_count >= 5 and output_seen and last_event_type != "waiting":
                        tail = strip_ansi(buf[-200:].decode("utf-8", errors="replace")).rstrip()
                        if tail and _SHELL_PROMPT_RE.search(tail):
                            self.log_event(session, "session.agent_exited", {"terminal_id": terminal_id})
                            active_terms = [tid for tid in session.terminal_ids if tid != terminal_id and tm.get(tid)]
                            if not active_terms:
                                summary = await self._generate_summary(session)
                                end_status = self.successful_execution_status(session)
                                self.update_status(session.id, end_status, output_summary=summary)
                                components = [
                                    {"type": "task_card", "title": session.description[:80], "status": end_status.value, "project": session.project},
                                ]
                                if summary:
                                    components.append({"type": "text", "content": summary})
                                cm.post_message(session.channel_id, role="system", content="Session completed.",
                                    metadata={"type": "a2ui", "components": components})
                            break

                    # Input waiting
                    if idle_count >= 3 and tm.is_waiting_for_input(terminal_id):
                        if last_event_type != "waiting":
                            last_event_type = "waiting"
                            self._note_waiting_for_input(session, terminal_id, cm)
                    continue

                idle_count = 0
                output_seen = True
                self._last_output_pos[pos_key] = len(buf)
                text = strip_ansi(buf[last_pos:].decode("utf-8", errors="replace"))

                # Check for exit signal (instant completion)
                exit_match = _EXIT_SIGNAL_RE.search(text)
                if exit_match:
                    exit_code = int(exit_match.group(1))
                    end_status = self.successful_execution_status(session) if exit_code == 0 else SessionStatus.FAILED
                    self.log_event(session, "session.agent_exited", {"terminal_id": terminal_id, "exit_code": exit_code})
                    # Save log + generate summary
                    self._save_terminal_log(session)
                    summary = await self._generate_summary(session)
                    self.update_status(session.id, end_status, output_summary=summary)
                    components: list[dict] = [
                        {"type": "task_card", "title": session.description[:80], "status": end_status.value, "project": session.project},
                    ]
                    if summary:
                        components.append({"type": "text", "content": summary})
                    cm.post_message(session.channel_id, role="system",
                        content=f"Session {'completed' if exit_code == 0 else 'failed'} (exit {exit_code}).",
                        metadata={"type": "a2ui", "components": components})
                    break

                # Check for input prompts in new output
                for pattern in _INPUT_PATTERNS:
                    if pattern.search(text[-500:]):
                        if last_event_type != "waiting":
                            last_event_type = "waiting"
                            self._note_waiting_for_input(session, terminal_id, cm)
                        break

                # Resume from waiting
                if session.status == SessionStatus.WAITING and last_event_type != "waiting":
                    session.blocker = None
                    self._persist(session)
                    self.update_status(session.id, SessionStatus.RUNNING)

            except Exception:
                logger.debug("Monitor read error for %s/%s", session.id, terminal_id, exc_info=True)

        # Cleanup
        monitor_key = f"{session.id}:{terminal_id}"
        self._monitors.pop(monitor_key, None)
        self._last_output_pos.pop(pos_key, None)

    def _save_terminal_log(self, session: Session):
        """Save terminal output buffer to log file for post-session viewing."""
        try:
            tm = get_terminal_manager()
            log_dir = get_rdc_home() / "sessions"
            log_dir.mkdir(parents=True, exist_ok=True)

            for tid in session.terminal_ids:
                term = tm.get(tid)
                if term and hasattr(term, "_output_buffer"):
                    buf = bytes(term._output_buffer)
                    if buf:
                        text = strip_ansi(buf.decode("utf-8", errors="replace"))
                        log_file = log_dir / f"{session.id}.log"
                        log_file.write_text(text)
                        break  # Save first terminal's output
        except Exception:
            logger.debug("Failed to save terminal log", exc_info=True)

    async def _generate_summary(self, session: Session) -> str:
        """Generate an outcome from the agent report and any working-tree changes."""
        self._save_terminal_log(session)
        try:
            log_file = get_rdc_home() / "sessions" / f"{session.id}.log"
            report = extract_agent_report(log_file.read_text(errors="replace"), session.agent_provider) if log_file.exists() else ""
            repo = get_project_repo()
            p = repo.get(session.project)
            if not p or not p.path:
                return report

            result = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                capture_output=True, text=True, cwd=p.path, timeout=10,
            )
            diff_stat = result.stdout.strip() if result.returncode == 0 else ""

            parts = []
            if report:
                parts.append(report)
            if diff_stat:
                parts.append(f"**Files changed:**\n```\n{diff_stat}\n```")
            if not parts:
                parts.append("The agent finished without recording a final report or changing files.")
            return "\n\n".join(parts)
        except Exception:
            logger.debug("Failed to generate session summary", exc_info=True)
            return ""

    def stop_monitor(self, session_id: str):
        """Stop all monitors for a session."""
        to_remove = [k for k in self._monitors if k.startswith(f"{session_id}:")]
        for k in to_remove:
            task = self._monitors.pop(k, None)
            if task:
                task.cancel()

    # ── Startup Recovery ──

    def recover_sessions(self):
        """Recover active sessions from DB on server startup."""
        tm = get_terminal_manager()
        for session in self.list_active():
            self._sessions[session.id] = session
            # Recover terminal IDs from junction table
            session.terminal_ids = self.get_terminal_ids(session.id)
            if session.status in (SessionStatus.RUNNING, SessionStatus.WAITING):
                alive_terms = [tid for tid in session.terminal_ids if tm.get(tid)]
                if not alive_terms:
                    end_status = SessionStatus.FAILED
                    self.update_status(
                        session.id,
                        end_status,
                        output_summary=session.output_summary or "Session ended — terminal process was terminated while unmonitored."
                    )
                    logger.info("Reconciled dead session %s to %s", session.id, end_status)
                else:
                    for tid in alive_terms:
                        self._start_terminal_monitor(session, tid)
                    logger.info("Recovered session %s with %d terminals", session.id, len(alive_terms))

    # ── DB Helpers ──

    def _persist(self, session: Session):
        db = self._db()
        # Keep terminal_id as first terminal for backward compat
        terminal_id = session.terminal_ids[0] if session.terminal_ids else None
        db.execute(
            """INSERT INTO sessions
               (id, channel_id, project, terminal_id, task_id, description, status,
                agent_provider, created_at, updated_at, completed_at, output_summary, metadata,
                goal, acceptance_criteria, control_owner, current_step, blocker, policy,
                budget, unresolved_risks)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 channel_id = excluded.channel_id,
                 project = excluded.project,
                 terminal_id = excluded.terminal_id,
                 task_id = excluded.task_id,
                 description = excluded.description,
                 status = excluded.status,
                 agent_provider = excluded.agent_provider,
                 updated_at = excluded.updated_at,
                 completed_at = excluded.completed_at,
                 output_summary = excluded.output_summary,
                 metadata = excluded.metadata,
                 goal = excluded.goal,
                 acceptance_criteria = excluded.acceptance_criteria,
                 control_owner = excluded.control_owner,
                 current_step = excluded.current_step,
                 blocker = excluded.blocker,
                 policy = excluded.policy,
                 budget = excluded.budget,
                 unresolved_risks = excluded.unresolved_risks""",
            (
                session.id, session.channel_id, session.project,
                terminal_id, session.task_id, session.description,
                session.status.value if isinstance(session.status, SessionStatus) else session.status,
                session.agent_provider,
                session.created_at.isoformat(), session.updated_at.isoformat(),
                session.completed_at.isoformat() if session.completed_at else None,
                session.output_summary,
                json.dumps(session.metadata) if session.metadata else None,
                session.goal or session.description,
                json.dumps(session.acceptance_criteria),
                session.control_owner,
                session.current_step,
                session.blocker,
                json.dumps(session.policy),
                json.dumps(session.budget) if session.budget else None,
                json.dumps(session.unresolved_risks),
            ),
        )
        db.commit()

    def _load(self, session_id: str) -> Optional[Session]:
        db = self._db()
        row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        session = self._row_to_session(row)
        self._sessions[session.id] = session
        return session

    def _rows_to_sessions(self, rows) -> list[Session]:
        """Convert multiple DB rows to Sessions with a single batch query for terminal IDs."""
        if not rows:
            return []

        session_ids = [r["id"] for r in rows]
        terminal_map: dict[str, list[str]] = defaultdict(list)

        # Batch-load all terminal IDs in one query
        try:
            db = self._db()
            placeholders = ",".join("?" for _ in session_ids)
            junc_rows = db.execute(
                f"SELECT session_id, terminal_id FROM session_terminals "
                f"WHERE session_id IN ({placeholders}) ORDER BY linked_at",
                session_ids,
            ).fetchall()
            for jr in junc_rows:
                terminal_map[jr["session_id"]].append(jr["terminal_id"])
        except Exception:
            pass

        return [self._row_to_session(r, terminal_map.get(r["id"])) for r in rows]

    def _row_to_session(self, row, preloaded_terminal_ids: Optional[list[str]] = None) -> Session:
        if preloaded_terminal_ids is not None:
            terminal_ids = preloaded_terminal_ids
        else:
            terminal_ids = []
            if row["terminal_id"]:
                terminal_ids = [row["terminal_id"]]
            # Fallback: query junction table (single-row loads only)
            try:
                db = self._db()
                junc = db.execute(
                    "SELECT terminal_id FROM session_terminals WHERE session_id = ? ORDER BY linked_at",
                    (row["id"],),
                ).fetchall()
                if junc:
                    terminal_ids = [r["terminal_id"] for r in junc]
            except Exception:
                pass

        # Fall back to legacy terminal_id column if junction table had nothing
        if not terminal_ids and row["terminal_id"]:
            terminal_ids = [row["terminal_id"]]

        try:
            status = SessionStatus(row["status"])
        except ValueError:
            status = SessionStatus.PENDING

        return Session(
            id=row["id"],
            channel_id=row["channel_id"],
            project=row["project"],
            terminal_ids=terminal_ids,
            task_id=row["task_id"],
            description=row["description"] or "",
            status=status,
            agent_provider=row["agent_provider"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            output_summary=row["output_summary"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            goal=row["goal"] or row["description"] or "",
            acceptance_criteria=json.loads(row["acceptance_criteria"] or "[]"),
            control_owner=row["control_owner"] or "none",
            current_step=row["current_step"],
            blocker=row["blocker"],
            policy=json.loads(row["policy"] or "{}"),
            budget=json.loads(row["budget"]) if row["budget"] else None,
            unresolved_risks=json.loads(row["unresolved_risks"] or "[]"),
        )


# ── Singleton ──

_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
