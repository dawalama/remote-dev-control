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

from .utils import (
    strip_ansi, safe_json_loads, json_field, enum_value,
    get_rdc_db, get_channel_manager, get_terminal_manager,
    get_state_machine, get_project_repo, get_rdc_home,
)

logger = logging.getLogger(__name__)


def build_agent_command(provider: str, description: str) -> str:
    """Build the terminal command for the provider the user actually selected."""
    prompt = shlex.quote(description[:4000])
    commands = {
        "claude": f"claude {prompt}",
        "codex": f"codex exec --sandbox workspace-write --ask-for-approval never {prompt}",
        "cursor": f"cursor-agent -p --trust {prompt}",
        "gemini": f"gemini --prompt-interactive {prompt}",
        "gwd": f"gemini --prompt-interactive {prompt}",
        "diagnostic": "printf 'RDC diagnostic session completed without project writes\\n'",
    }
    inner = description if provider == "shell" else commands.get(provider)
    if inner is None:
        raise ValueError(f"Unsupported session provider: {provider}")
    return f"trap 'echo __RDC_EXIT:$?' EXIT; {inner}"


def agent_provider_blocker(provider: str) -> str | None:
    """Return a user-facing blocker before creating a terminal for a provider."""
    checks = {
        "cursor": (["cursor-agent", "status"], "Cursor Agent", "cursor-agent login", "CURSOR_API_KEY"),
        "codex": (["codex", "login", "status"], "Codex CLI", "codex login", "OPENAI_API_KEY"),
        "claude": (["claude", "auth", "status"], "Claude Code", "claude auth login", "ANTHROPIC_API_KEY"),
    }
    if provider == "diagnostic":
        return None
    if provider in {"gemini", "gwd"}:
        if not shutil.which("gemini"):
            return "Gemini CLI is not installed on the RDC host."
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or (Path.home() / ".gemini" / "oauth_creds.json").exists()):
            return "Gemini CLI is installed but not authenticated. Run `gemini` on the RDC host or configure GEMINI_API_KEY."
        return None
    check = checks.get(provider)
    if not check:
        return f"Unsupported session provider: {provider}"
    command, label, setup_command, key_name = check
    if not shutil.which(command[0]):
        return f"{label} is not installed on the RDC host."
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return f"{label} is not installed on the RDC host."
    except subprocess.TimeoutExpired:
        return f"{label} authentication check timed out. Run `{setup_command}` on the RDC host."
    if result.returncode != 0:
        return f"{label} is not authenticated for RDC. Run `{setup_command}` on the RDC host or configure {key_name}."
    return None


def list_agent_provider_readiness() -> list[dict[str, Any]]:
    definitions = [
        ("codex", "Codex CLI", "codex", "codex login"),
        ("claude", "Claude Code", "claude", "claude auth login"),
        ("cursor", "Cursor Agent", "cursor-agent", "cursor-agent login"),
        ("gemini", "Gemini CLI", "gemini", "gemini"),
        ("diagnostic", "Diagnostic", None, None),
    ]
    providers = []
    for provider_id, label, executable, setup_command in definitions:
        installed = executable is None or shutil.which(executable) is not None
        blocker = agent_provider_blocker(provider_id) if installed else f"{label} is not installed on the RDC host."
        providers.append({
            "id": provider_id,
            "label": label,
            "installed": installed,
            "ready": blocker is None,
            "status": "ready" if blocker is None else ("needs_auth" if installed else "not_installed"),
            "detail": blocker,
            "setup_command": setup_command,
            "capabilities": ["plan", "write", "verify"] if provider_id != "diagnostic" else ["verify"],
        })
    return providers

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
        approval_id = f"approval-{secrets.token_hex(8)}"
        db.execute(
            """INSERT INTO session_approvals
               (id, session_id, action_type, risk_level, summary, details, requested_by)
               VALUES (?, ?, 'terminal_input', 'medium', ?, ?, ?)""",
            (approval_id, session.id, summary[-1000:], json.dumps({"terminal_id": terminal_id}), "agent"),
        )
        db.commit()
        self.log_event(session, "session.approval_requested", {"approval_id": approval_id, "summary": summary[-200:]})
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
    ) -> Session:
        """Create a new session and persist to DB."""
        session = Session(
            id=f"sess-{secrets.token_hex(6)}",
            channel_id=channel_id,
            project=project,
            description=description,
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

    def list_active(self) -> list[Session]:
        db = self._db()
        placeholders = ",".join("?" for _ in SessionStatus.active_values())
        rows = db.execute(
            f"SELECT * FROM sessions WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            SessionStatus.active_values(),
        ).fetchall()
        return self._rows_to_sessions(rows)

    def update_status(self, session_id: str, status: SessionStatus | str, output_summary: Optional[str] = None):
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
        self.log_event(session, f"session.{session.status.value}", {"summary": output_summary[:200] if output_summary else None})

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

        self._monitors[monitor_key] = asyncio.create_task(_monitor())

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
                if not active_terms and session.status == SessionStatus.RUNNING:
                    summary = await self._generate_summary(session)
                    end_status = self.successful_execution_status(session)
                    self.update_status(session.id, end_status, output_summary=summary)
                    components: list[dict] = [
                        {"type": "task_card", "title": session.description[:80], "status": end_status.value, "project": session.project},
                    ]
                    if summary:
                        components.append({"type": "text", "content": summary})
                    cm.post_message(session.channel_id, role="system", content="Session completed.",
                        metadata={"type": "a2ui", "components": components})
                break

            # Check terminal status
            term_status = term.status.value if hasattr(term.status, "value") else str(term.status)
            if term_status in ("stopped", "error"):
                self.log_event(session, "session.terminal_stopped", {"terminal_id": terminal_id, "status": term_status})
                active_terms = [tid for tid in session.terminal_ids if tm.get(tid) and tid != terminal_id]
                if not active_terms and session.status == SessionStatus.RUNNING:
                    summary = await self._generate_summary(session)
                    end_status = SessionStatus.FAILED if term_status == "error" else self.successful_execution_status(session)
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
                            session.status = SessionStatus.WAITING
                            self._persist(session)
                            last_event_type = "waiting"
                            tail = strip_ansi(buf[-512:].decode("utf-8", errors="replace"))
                            self.log_event(session, "session.waiting_for_input", {"terminal_id": terminal_id, "prompt": tail.strip()[-200:]})
                            self.request_approval(session, tail.strip(), terminal_id)
                            cm.post_message(session.channel_id, role="system", content="Agent is waiting for input",
                                metadata={"type": "a2ui", "components": [
                                    {"type": "text", "content": f"```\n{tail.strip()[-200:]}\n```"},
                                    {"type": "confirm", "title": "The agent needs your input", "description": "Open the terminal to respond.", "confirm_label": "Open Terminal", "cancel_label": "Dismiss"},
                                ]})
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
                            session.status = SessionStatus.WAITING
                            self._persist(session)
                            last_event_type = "waiting"
                            self.log_event(session, "session.waiting_for_input", {"terminal_id": terminal_id})
                            self.request_approval(session, text[-500:].strip(), terminal_id)
                            cm.post_message(session.channel_id, role="system", content="Agent is waiting for input",
                                metadata={"type": "a2ui", "components": [
                                    {"type": "text", "content": f"```\n{text[-300:].strip()}\n```"},
                                    {"type": "confirm", "title": "The agent needs your input", "confirm_label": "Open Terminal", "cancel_label": "Dismiss"},
                                ]})
                        break

                # Resume from waiting
                if session.status == SessionStatus.WAITING and last_event_type != "waiting":
                    session.status = SessionStatus.RUNNING
                    self._persist(session)

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
        """Generate a summary by checking git changes. Also saves terminal log."""
        self._save_terminal_log(session)
        try:
            repo = get_project_repo()
            p = repo.get(session.project)
            if not p or not p.path:
                return ""

            result = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                capture_output=True, text=True, cwd=p.path, timeout=10,
            )
            diff_stat = result.stdout.strip() if result.returncode == 0 else ""

            result2 = subprocess.run(
                ["git", "log", "--oneline", "-5", "--no-decorate"],
                capture_output=True, text=True, cwd=p.path, timeout=10,
            )
            recent_commits = result2.stdout.strip() if result2.returncode == 0 else ""

            parts = []
            if diff_stat:
                parts.append(f"**Files changed:**\n```\n{diff_stat}\n```")
            if recent_commits:
                parts.append(f"**Recent commits:**\n```\n{recent_commits}\n```")
            if not parts:
                parts.append("No file changes detected.")
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
        for session in self.list_active():
            self._sessions[session.id] = session
            # Recover terminal IDs from junction table
            session.terminal_ids = self.get_terminal_ids(session.id)
            if session.status in (SessionStatus.RUNNING, SessionStatus.WAITING):
                for tid in session.terminal_ids:
                    self._start_terminal_monitor(session, tid)
                if session.terminal_ids:
                    logger.info("Recovered session %s with %d terminals", session.id, len(session.terminal_ids))

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
