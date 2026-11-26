"""Native Agent Worker Lifecycle ("The Herd") & Agent Task Board for RDC.

Supports two distinct operating modes:
1. Session Mode: 1 Goal = 1 Audited Session pipeline (worktree, verify loop, proof artifact).
2. Agent Mode: Persistent Resident Agent(s) that you can chat with directly, attach to in
   a live terminal, and that can create and complete multiple lightweight Task Cards on
   a shared Task Board via `rdc-task`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .db.repositories import get_project_repo
from .utils import get_rdc_db, get_rdc_home, get_terminal_manager, strip_ansi

logger = logging.getLogger(__name__)

AGENT_STATUS_IDLE = "idle"
AGENT_STATUS_WORKING = "working"
AGENT_STATUS_WAITING = "waiting"
AGENT_STATUS_STOPPED = "stopped"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_RDC_TASK_SCRIPT = """#!/usr/bin/env python3
\"\"\"rdc-task: Lightweight CLI for RDC Resident Agents to manage the shared Task Board.\"\"\"
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print("Usage:")
        print("  rdc-task list")
        print("  rdc-task pull")
        print("  rdc-task add <title>")
        print("  rdc-task start <task_id>")
        print("  rdc-task done [task_id] [summary_note]")
        sys.exit(0)

    cmd = args[0].lower()
    rdc_home = Path(os.environ.get("RDC_HOME") or (Path.home() / ".rdc"))
    db_path = Path(os.environ.get("RDC_DB_PATH") or (rdc_home / "rdc.db"))
    channel_id = os.environ.get("RDC_CHANNEL_ID") or "ch-system"
    project = os.environ.get("RDC_PROJECT") or "default"
    agent_id = os.environ.get("RDC_AGENT_ID") or None
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        \"\"\"CREATE TABLE IF NOT EXISTS agent_tasks (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            project TEXT NOT NULL,
            agent_id TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'todo',
            result_note TEXT,
            created_by TEXT NOT NULL DEFAULT 'agent',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )\"\"\"
    )

    if cmd == "list":
        rows = conn.execute(
            "SELECT id, status, title, result_note FROM agent_tasks WHERE channel_id = ? AND project = ? ORDER BY created_at ASC",
            (channel_id, project),
        ).fetchall()
        if not rows:
            print("No tasks on board.")
        for r in rows:
            note = f" — {r['result_note']}" if r["result_note"] else ""
            print(f"[{r['status'].upper()}] {r['id']}: {r['title']}{note}")
        sys.exit(0)

    if cmd in ("pull", "next"):
        row = conn.execute(
            "SELECT id, title FROM agent_tasks WHERE channel_id = ? AND project = ? AND status = 'todo' ORDER BY created_at ASC LIMIT 1",
            (channel_id, project),
        ).fetchone()
        if not row:
            print("No todo tasks available to pull.")
            sys.exit(0)
        tid = row["id"]
        conn.execute(
            "UPDATE agent_tasks SET status = 'in_progress', agent_id = COALESCE(?, agent_id), updated_at = ? WHERE id = ?",
            (agent_id, now, tid),
        )
        conn.commit()
        print(f"PULLED {tid}: {row['title']}")
        sys.exit(0)

    if cmd == "add":
        title = " ".join(args[1:]).strip()
        if not title:
            print("Error: task title required", file=sys.stderr)
            sys.exit(1)
        tid = f"task-{secrets.token_hex(3)}"
        conn.execute(
            \"\"\"INSERT INTO agent_tasks (id, channel_id, project, agent_id, title, status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'todo', 'agent', ?, ?)\"\"\",
            (tid, channel_id, project, agent_id, title, now, now),
        )
        conn.commit()
        print(f"Created {tid}: {title}")
        sys.exit(0)

    if cmd in ("start", "in_progress", "doing"):
        if len(args) < 2:
            print("Error: task_id required", file=sys.stderr)
            sys.exit(1)
        tid = args[1].strip()
        conn.execute(
            "UPDATE agent_tasks SET status = 'in_progress', agent_id = COALESCE(?, agent_id), updated_at = ? WHERE id = ?",
            (agent_id, now, tid),
        )
        conn.commit()
        print(f"Started {tid}")
        sys.exit(0)

    if cmd in ("done", "complete", "completed"):
        if len(args) >= 2 and args[1].strip().startswith("task-"):
            tid = args[1].strip()
            note = " ".join(args[2:]).strip() or None
        else:
            row = conn.execute(
                "SELECT id FROM agent_tasks WHERE channel_id = ? AND project = ? AND status = 'in_progress' ORDER BY updated_at DESC LIMIT 1",
                (channel_id, project),
            ).fetchone()
            if not row:
                print("Error: task_id required (no in_progress task found)", file=sys.stderr)
                sys.exit(1)
            tid = row["id"]
            note = " ".join(args[1:]).strip() or None
        conn.execute(
            "UPDATE agent_tasks SET status = 'done', result_note = COALESCE(?, result_note), agent_id = COALESCE(?, agent_id), updated_at = ? WHERE id = ?",
            (note, agent_id, now, tid),
        )
        conn.commit()
        print(f"Completed {tid}")
        sys.exit(0)

    print(f"Unknown command: {cmd}", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
"""


def ensure_rdc_task_cli() -> Path:
    """Install `~/.rdc/bin/rdc-task` so agents can create and complete board tasks from shell."""
    bin_dir = Path.home() / ".rdc" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = bin_dir / "rdc-task"
    try:
        if not script_path.exists() or script_path.read_text(encoding="utf-8") != _RDC_TASK_SCRIPT:
            script_path.write_text(_RDC_TASK_SCRIPT, encoding="utf-8")
            script_path.chmod(0o755)
    except OSError:
        logger.debug("Could not write rdc-task helper script", exc_info=True)
    return script_path


@dataclass
class AgentTaskCard:
    id: str
    channel_id: str
    project: str
    agent_id: Optional[str]
    title: str
    status: str = "todo"  # "todo" | "in_progress" | "done"
    result_note: Optional[str] = None
    created_by: str = "user"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "project": self.project,
            "agent_id": self.agent_id,
            "title": self.title,
            "status": self.status,
            "result_note": self.result_note,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _get_terminal_state(terminal_id: Optional[str]) -> str:
    """Inspect live PTY state for an agent's terminal without throwing."""
    if not terminal_id:
        return "none"
    try:
        from .terminal import get_terminal_manager
        import time

        tm = get_terminal_manager()
        session = tm.get(terminal_id)
        if not session or not tm.is_alive(terminal_id):
            return "exited"
        last_out = getattr(session, "_last_output_at", 0.0) or 0.0
        if last_out > 0.0 and (time.monotonic() - last_out) < 4.0:
            return "streaming"
        return "idle"
    except Exception:
        return "none"


@dataclass
class AgentWorker:
    id: str
    name: str
    channel_id: str
    project: str
    provider: str
    status: str = AGENT_STATUS_IDLE
    current_session_id: Optional[str] = None
    last_session_id: Optional[str] = None
    terminal_id: Optional[str] = None
    worktree_path: Optional[str] = None
    worktree_branch: Optional[str] = None
    context_mode: str = "warm"  # "warm" | "fresh"
    auto_drain: bool = False
    sessions_completed: int = 0
    turns_completed: int = 0
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "channel_id": self.channel_id,
            "project": self.project,
            "provider": self.provider,
            "status": self.status,
            "current_session_id": self.current_session_id,
            "last_session_id": self.last_session_id,
            "terminal_id": self.terminal_id,
            "terminal_state": _get_terminal_state(self.terminal_id),
            "worktree_path": self.worktree_path,
            "worktree_branch": self.worktree_branch,
            "context_mode": self.context_mode,
            "auto_drain": self.auto_drain,
            "sessions_completed": self.sessions_completed,
            "turns_completed": self.turns_completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "messages": (self.metadata or {}).get("messages") or [],
        }


class AgentHerdManager:
    """Manages persistent Agent Workers and their lightweight Task Board."""

    def __init__(self) -> None:
        self._schema_ready = False
        self._chat_monitors: dict[str, asyncio.Task] = {}

    def _db(self):
        db = get_rdc_db()
        if not self._schema_ready:
            self._ensure_schema(db)
            self._schema_ready = True
        return db

    def _active_db_path(self) -> Path:
        try:
            row = self._db().execute("PRAGMA database_list").fetchone()
            if row and row["file"]:
                return Path(row["file"]).resolve()
        except Exception:
            pass
        return get_rdc_home() / "rdc.db"

    @staticmethod
    def _ensure_schema(db) -> None:
        db.execute(
            """CREATE TABLE IF NOT EXISTS agent_workers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                project TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'idle',
                current_session_id TEXT,
                last_session_id TEXT,
                terminal_id TEXT,
                worktree_path TEXT,
                worktree_branch TEXT,
                context_mode TEXT NOT NULL DEFAULT 'warm',
                auto_drain INTEGER NOT NULL DEFAULT 0,
                sessions_completed INTEGER NOT NULL DEFAULT 0,
                turns_completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_workers_channel ON agent_workers(channel_id, status)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_workers_session ON agent_workers(current_session_id)"
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                project TEXT NOT NULL,
                agent_id TEXT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'todo',
                result_note TEXT,
                created_by TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_channel ON agent_tasks(channel_id, project, status)"
        )
        db.commit()

    @staticmethod
    def _row_to_worker(row) -> AgentWorker:
        meta_raw = row["metadata"]
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except Exception:
            meta = {}
        return AgentWorker(
            id=row["id"],
            name=row["name"],
            channel_id=row["channel_id"],
            project=row["project"],
            provider=row["provider"],
            status=row["status"] or AGENT_STATUS_IDLE,
            current_session_id=row["current_session_id"],
            last_session_id=row["last_session_id"],
            terminal_id=row["terminal_id"],
            worktree_path=row["worktree_path"],
            worktree_branch=row["worktree_branch"],
            context_mode=row["context_mode"] or "warm",
            auto_drain=bool(row["auto_drain"]),
            sessions_completed=int(row["sessions_completed"] or 0),
            turns_completed=int(row["turns_completed"] or 0),
            created_at=row["created_at"] or _now_iso(),
            updated_at=row["updated_at"] or _now_iso(),
            metadata=meta if isinstance(meta, dict) else {},
        )

    @staticmethod
    def _row_to_task(row) -> AgentTaskCard:
        return AgentTaskCard(
            id=row["id"],
            channel_id=row["channel_id"],
            project=row["project"],
            agent_id=row["agent_id"],
            title=row["title"],
            status=row["status"] or "todo",
            result_note=row["result_note"],
            created_by=row["created_by"] or "user",
            created_at=row["created_at"] or _now_iso(),
            updated_at=row["updated_at"] or _now_iso(),
        )

    def _persist(self, worker: AgentWorker) -> AgentWorker:
        worker.updated_at = _now_iso()
        db = self._db()
        db.execute(
            """INSERT INTO agent_workers (
                id, name, channel_id, project, provider, status,
                current_session_id, last_session_id, terminal_id,
                worktree_path, worktree_branch, context_mode,
                auto_drain, sessions_completed, turns_completed,
                created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                channel_id = excluded.channel_id,
                project = excluded.project,
                provider = excluded.provider,
                status = excluded.status,
                current_session_id = excluded.current_session_id,
                last_session_id = excluded.last_session_id,
                terminal_id = excluded.terminal_id,
                worktree_path = excluded.worktree_path,
                worktree_branch = excluded.worktree_branch,
                context_mode = excluded.context_mode,
                auto_drain = excluded.auto_drain,
                sessions_completed = excluded.sessions_completed,
                turns_completed = excluded.turns_completed,
                updated_at = excluded.updated_at,
                metadata = excluded.metadata""",
            (
                worker.id,
                worker.name,
                worker.channel_id,
                worker.project,
                worker.provider,
                worker.status,
                worker.current_session_id,
                worker.last_session_id,
                worker.terminal_id,
                worker.worktree_path,
                worker.worktree_branch,
                worker.context_mode,
                1 if worker.auto_drain else 0,
                worker.sessions_completed,
                worker.turns_completed,
                worker.created_at,
                worker.updated_at,
                json.dumps(worker.metadata or {}),
            ),
        )
        db.commit()
        return worker

    def get(self, agent_id: str) -> Optional[AgentWorker]:
        row = self._db().execute(
            "SELECT * FROM agent_workers WHERE id = ?",
            (agent_id,),
        ).fetchone()
        return self._row_to_worker(row) if row else None

    def get_by_session(self, session_id: str) -> Optional[AgentWorker]:
        row = self._db().execute(
            "SELECT * FROM agent_workers WHERE current_session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        return self._row_to_worker(row) if row else None

    def list_workers(
        self,
        *,
        channel_id: Optional[str] = None,
        project: Optional[str] = None,
        include_stopped: bool = False,
    ) -> list[AgentWorker]:
        clauses: list[str] = []
        params: list[Any] = []
        if channel_id:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        if project:
            clauses.append("project = ?")
            params.append(project)
        if not include_stopped:
            clauses.append("status != ?")
            params.append(AGENT_STATUS_STOPPED)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db().execute(
            f"SELECT * FROM agent_workers {where_sql} ORDER BY created_at ASC",
            params,
        ).fetchall()
        return [self._row_to_worker(r) for r in rows]

    def _next_worker_name(self, channel_id: str, provider: str) -> str:
        existing = self.list_workers(channel_id=channel_id, include_stopped=True)
        same_provider = [w for w in existing if w.provider == provider]
        return f"{provider}-{len(same_provider) + 1}"

    def spawn_worker(
        self,
        *,
        channel_id: str,
        project: str,
        provider: str,
        name: Optional[str] = None,
        auto_drain: bool = False,
        context_mode: str = "warm",
        worktree_path: Optional[str] = None,
        worktree_branch: Optional[str] = None,
    ) -> AgentWorker:
        """Create and register a resident Agent Worker on a workstream/project."""
        clean_provider = (provider or "codex").strip()
        if clean_provider == "orchestrated":
            from .agent_registry import load_agent_provider_policy

            clean_provider = load_agent_provider_policy().get("preferred_provider", "codex")
        worker_name = (name or "").strip() or self._next_worker_name(channel_id, clean_provider)
        if not worktree_path:
            rec = get_project_repo().get(project)
            if rec and rec.path:
                worktree_path = str(Path(rec.path).resolve())
        worker = AgentWorker(
            id=f"agent-{secrets.token_hex(4)}",
            name=worker_name,
            channel_id=channel_id,
            project=project,
            provider=clean_provider,
            status=AGENT_STATUS_IDLE,
            worktree_path=worktree_path,
            worktree_branch=worktree_branch,
            context_mode="fresh" if context_mode == "fresh" else "warm",
            auto_drain=bool(auto_drain),
            metadata={"messages": []},
        )
        ensure_rdc_task_cli()
        return self._persist(worker)

    # ── Task Board CRUD (Agent Mode) ────────────────────────────────────────

    def list_tasks(
        self,
        *,
        channel_id: Optional[str] = None,
        project: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> list[AgentTaskCard]:
        clauses: list[str] = []
        params: list[Any] = []
        if channel_id:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        if project:
            clauses.append("project = ?")
            params.append(project)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db().execute(
            f"SELECT * FROM agent_tasks {where_sql} ORDER BY created_at ASC",
            params,
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def create_task(
        self,
        *,
        channel_id: str,
        project: str,
        title: str,
        agent_id: Optional[str] = None,
        status: str = "todo",
        created_by: str = "user",
    ) -> AgentTaskCard:
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Task title is required")
        valid_status = status if status in ("todo", "in_progress", "done") else "todo"
        card = AgentTaskCard(
            id=f"task-{secrets.token_hex(3)}",
            channel_id=channel_id,
            project=project,
            agent_id=agent_id,
            title=clean_title,
            status=valid_status,
            created_by=created_by,
        )
        db = self._db()
        db.execute(
            """INSERT INTO agent_tasks (id, channel_id, project, agent_id, title, status, result_note, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card.id,
                card.channel_id,
                card.project,
                card.agent_id,
                card.title,
                card.status,
                card.result_note,
                card.created_by,
                card.created_at,
                card.updated_at,
            ),
        )
        db.commit()
        return card

    def update_task(
        self,
        task_id: str,
        *,
        title: Optional[str] = None,
        status: Optional[str] = None,
        result_note: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> AgentTaskCard:
        db = self._db()
        row = db.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise LookupError(f"Task not found: {task_id}")
        card = self._row_to_task(row)
        was_done = card.status == "done"
        if title is not None and title.strip():
            card.title = title.strip()
        if status in ("todo", "in_progress", "done"):
            card.status = status
        if result_note is not None:
            card.result_note = result_note.strip() or None
        if agent_id is not None:
            card.agent_id = agent_id or None
        card.updated_at = _now_iso()
        db.execute(
            """UPDATE agent_tasks
               SET title = ?, status = ?, result_note = ?, agent_id = ?, updated_at = ?
               WHERE id = ?""",
            (card.title, card.status, card.result_note, card.agent_id, card.updated_at, card.id),
        )
        db.commit()
        if not was_done and card.status == "done" and card.agent_id:
            w = self.get(card.agent_id)
            if w:
                w.sessions_completed += 1
                self._persist(w)
        return card

    def delete_task(self, task_id: str) -> bool:
        db = self._db()
        cur = db.execute("DELETE FROM agent_tasks WHERE id = ?", (task_id,))
        db.commit()
        return cur.rowcount > 0

    # ── Direct Agent Chat & Live Terminal (Agent Mode) ──────────────────────

    def ensure_interactive_terminal(self, agent_id: str) -> dict[str, Any]:
        """Return an active interactive CLI terminal for this agent, spawning one if needed."""
        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")

        tm = get_terminal_manager()
        if worker.terminal_id:
            existing_term = tm.get(worker.terminal_id)
            if existing_term:
                t_status = existing_term.status.value if hasattr(existing_term.status, "value") else str(existing_term.status)
                if t_status == "running":
                    return {"terminal_id": existing_term.id, "worker": worker.to_dict()}

        ensure_rdc_task_cli()
        rec = get_project_repo().get(worker.project)
        cwd = worker.worktree_path or (str(Path(rec.path).resolve()) if rec and rec.path else None)

        from .agent_registry import resolve_provider_spec

        _driver, _inv, _alias, spec = resolve_provider_spec(worker.provider)
        cli_cmd = spec.terminal_command or spec.executable or worker.provider
        if worker.provider in ("diagnostic", "shell"):
            cli_cmd = os.environ.get("SHELL") or "/bin/zsh"

        rdc_home = get_rdc_home()
        db_path = self._active_db_path()
        env_exports = (
            f"export RDC_AGENT_ID={shlex.quote(worker.id)} "
            f"RDC_CHANNEL_ID={shlex.quote(worker.channel_id)} "
            f"RDC_PROJECT={shlex.quote(worker.project)} "
            f"RDC_HOME={shlex.quote(str(rdc_home))} "
            f"RDC_DB_PATH={shlex.quote(str(db_path))}; "
        )
        term = tm.create(
            project=worker.project,
            command=f"{env_exports}{cli_cmd}",
            cwd=cwd,
            is_agent=True,
        )
        worker.terminal_id = term.id
        self._persist(worker)
        return {"terminal_id": term.id, "worker": worker.to_dict()}

    def send_to_worker(
        self,
        agent_id: str,
        text: str,
        *,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Paste text and press Enter in the agent's live terminal (like `hc send <t> <text>`)."""
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("Text is required")
        ensured = self.ensure_interactive_terminal(agent_id)
        terminal_id = ensured["terminal_id"]
        if task_id:
            try:
                self.update_task(task_id, status="in_progress", agent_id=agent_id)
            except LookupError:
                pass
        tm = get_terminal_manager()
        tm.write(terminal_id, f"{clean_text}\r".encode("utf-8"))
        worker = self.get(agent_id)
        return {
            "success": True,
            "agent_id": agent_id,
            "terminal_id": terminal_id,
            "worker": worker.to_dict() if worker else ensured["worker"],
        }

    def pull_next_task_for_worker(self, agent_id: str) -> dict[str, Any]:
        """Claim the oldest Todo task for this agent's channel/project and send it into its live terminal."""
        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")
        todo = [
            t
            for t in self.list_tasks(channel_id=worker.channel_id, project=worker.project)
            if t.status == "todo"
        ]
        if not todo:
            raise ValueError("No Todo tasks on the board to pull")
        card = todo[0]
        updated = self.update_task(card.id, status="in_progress", agent_id=worker.id)
        prompt = (
            f"Work on task {updated.id}: {updated.title} "
            f"(when finished, run `rdc-task done {updated.id} \"<summary>\"`)"
        )
        sent = self.send_to_worker(worker.id, prompt, task_id=updated.id)
        return {**sent, "task": updated.to_dict()}

    def get_worker_clean_tail(self, agent_id: str, max_lines: int = 18) -> list[str]:
        """Return ANSI-stripped recent output lines for non-terminal UIs like Telegram."""
        worker = self.get(agent_id)
        if not worker or not worker.terminal_id:
            return []
        try:
            import re
            from .terminal import get_terminal_manager, _ANSI_ESCAPE

            tm = get_terminal_manager()
            raw = tm.get_buffer(worker.terminal_id)
            if not raw:
                return []
            text = raw.decode("utf-8", errors="replace")
            # Strip ANSI CSI/OSC sequences and control chars
            clean = _ANSI_ESCAPE.sub("", text)
            clean = re.sub(r"\x1b[()][A-Za-z0-9]|\x1b[=>c]", "", clean)
            lines: list[str] = []
            for raw_line in clean.splitlines():
                # Handle carriage-return progress overwrites by keeping final segment
                seg = raw_line.split("\r")[-1].strip()
                if seg:
                    lines.append(seg[:140])
            return lines[-max_lines:]
        except Exception:
            return []

    def chat_with_worker(self, agent_id: str, message: str) -> dict[str, Any]:
        """Send a direct prompt/instruction to a Resident Agent and have it update the Task Board."""
        text = (message or "").strip()
        if not text:
            raise ValueError("Message is required")
        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")

        ensure_rdc_task_cli()
        tm = get_terminal_manager()

        # If an old chat turn terminal is finished or still open, clean it up before starting the new turn
        if worker.terminal_id:
            old_term = tm.get(worker.terminal_id)
            if old_term:
                try:
                    tm.destroy(worker.terminal_id)
                except Exception:
                    pass

        rec = get_project_repo().get(worker.project)
        cwd_path = (
            Path(worker.worktree_path).resolve()
            if worker.worktree_path and Path(worker.worktree_path).is_dir()
            else (Path(rec.path).resolve() if rec and rec.path and Path(rec.path).is_dir() else None)
        )

        # Build current task board context so the agent sees what is on the board
        current_tasks = self.list_tasks(channel_id=worker.channel_id, project=worker.project)
        board_lines = []
        for t in current_tasks[:30]:
            board_lines.append(f"- [{t.status.upper()}] {t.id}: {t.title}")
        board_block = "\n".join(board_lines) if board_lines else "(Board is currently empty)"

        agent_mode_note = (
            f"\n\n[RDC Agent Mode — Resident Agent `{worker.name}`]\n"
            f"You have a live shared Task Board with the operator via the `rdc-task` CLI in your PATH:\n"
            f"  • `rdc-task add \"<task title>\"` — add a new task card to the board\n"
            f"  • `rdc-task start <task-id>` — mark a task card as In Progress\n"
            f"  • `rdc-task done <task-id> \"<brief note>\"` — mark a task card as Done\n"
            f"  • `rdc-task list` — view current tasks\n"
            f"Current Task Board state:\n{board_block}\n"
            f"Break multi-step work into task cards with `rdc-task add` and mark them complete with `rdc-task done` as you finish them."
        )

        full_prompt = f"{text}{agent_mode_note}"
        use_resume = worker.context_mode == "warm" and worker.turns_completed > 0

        from .session_manager import build_agent_command

        raw_cmd = build_agent_command(
            worker.provider,
            full_prompt,
            resume=use_resume,
            fallback_description=full_prompt,
        )

        rdc_home = get_rdc_home()
        db_path = self._active_db_path()
        env_exports = (
            f"export RDC_AGENT_ID={shlex.quote(worker.id)} "
            f"RDC_CHANNEL_ID={shlex.quote(worker.channel_id)} "
            f"RDC_PROJECT={shlex.quote(worker.project)} "
            f"RDC_HOME={shlex.quote(str(rdc_home))} "
            f"RDC_DB_PATH={shlex.quote(str(db_path))}; "
        )
        command = f"{env_exports}{raw_cmd}"

        term = tm.create(
            project=worker.project,
            command=command,
            cwd=str(cwd_path) if cwd_path else None,
        )

        messages = list((worker.metadata or {}).get("messages") or [])
        user_msg = {
            "id": f"msg-{secrets.token_hex(4)}",
            "role": "user",
            "content": text,
            "timestamp": _now_iso(),
        }
        messages.append(user_msg)
        worker.metadata = {**(worker.metadata or {}), "messages": messages[-50:]}
        worker.status = AGENT_STATUS_WORKING
        worker.terminal_id = term.id
        if cwd_path:
            worker.worktree_path = str(cwd_path)
        self._persist(worker)

        self._start_worker_turn_monitor(worker.id, term.id)
        return {
            "success": True,
            "agent_id": worker.id,
            "terminal_id": term.id,
            "worker": worker.to_dict(),
        }

    def _start_worker_turn_monitor(self, agent_id: str, terminal_id: str) -> None:
        key = f"{agent_id}:{terminal_id}"
        old_task = self._chat_monitors.pop(key, None)
        if old_task:
            old_task.cancel()

        async def _watch() -> None:
            from .session_manager import _EXIT_SIGNAL_RE, extract_agent_report

            tm = get_terminal_manager()
            while True:
                await asyncio.sleep(2)
                w = self.get(agent_id)
                if not w or w.status == AGENT_STATUS_STOPPED or w.terminal_id != terminal_id:
                    break
                term = tm.get(terminal_id)
                if not term:
                    self._finish_worker_turn(agent_id, terminal_id, "")
                    break
                buf = bytes(getattr(term, "_output_buffer", b""))
                text = strip_ansi(buf.decode("utf-8", errors="replace"))
                t_status = term.status.value if hasattr(term.status, "value") else str(term.status)
                if _EXIT_SIGNAL_RE.search(text) or t_status in ("stopped", "error"):
                    report = extract_agent_report(text, w.provider)
                    if not report:
                        lines = [
                            ln.strip()
                            for ln in text.splitlines()
                            if ln.strip()
                            and not ln.strip().startswith("__RDC_")
                            and not ln.strip().startswith("trap ")
                        ]
                        report = "\n".join(lines[-15:]) if lines else "Finished turn."
                    self._finish_worker_turn(agent_id, terminal_id, report)
                    break

        try:
            loop = asyncio.get_running_loop()
            self._chat_monitors[key] = loop.create_task(_watch())
        except RuntimeError:
            pass

    def _finish_worker_turn(self, agent_id: str, terminal_id: str, report: str) -> None:
        w = self.get(agent_id)
        if not w:
            return
        messages = list((w.metadata or {}).get("messages") or [])
        if report.strip():
            messages.append({
                "id": f"msg-{secrets.token_hex(4)}",
                "role": "assistant",
                "content": report.strip(),
                "timestamp": _now_iso(),
            })
        w.metadata = {**(w.metadata or {}), "messages": messages[-50:]}
        w.status = AGENT_STATUS_IDLE
        w.turns_completed += 1
        if w.context_mode == "fresh":
            w.context_mode = "warm"
        # Count completed tasks attributed to this agent
        done_tasks = [
            t for t in self.list_tasks(channel_id=w.channel_id, project=w.project, agent_id=w.id)
            if t.status == "done"
        ]
        if len(done_tasks) > w.sessions_completed:
            w.sessions_completed = len(done_tasks)
        self._persist(w)

    # ── Session Mode Integration ────────────────────────────────────────────

    def acquire_for_session(
        self,
        session: Any,
        *,
        effective_provider: str,
        preferred_agent_id: Optional[str] = None,
    ) -> AgentWorker:
        """Bind a starting Session to a specific or idle Agent Worker, or spawn one."""
        meta = dict(getattr(session, "metadata", None) or {})
        target_agent_id = preferred_agent_id or meta.get("agent_id")

        worker: Optional[AgentWorker] = None
        if target_agent_id:
            candidate = self.get(str(target_agent_id))
            if candidate and candidate.status != AGENT_STATUS_STOPPED:
                if candidate.current_session_id in (None, session.id) or candidate.status == AGENT_STATUS_IDLE:
                    worker = candidate

        if worker is None:
            worker = self.get_by_session(session.id)

        if worker is None:
            idle_candidates = [
                w
                for w in self.list_workers(channel_id=session.channel_id, project=session.project)
                if w.status == AGENT_STATUS_IDLE and w.provider == effective_provider
            ]
            if idle_candidates:
                worker = idle_candidates[0]

        if worker is None:
            worker = self.spawn_worker(
                channel_id=session.channel_id,
                project=session.project,
                provider=effective_provider,
            )

        worker.provider = effective_provider
        worker.status = AGENT_STATUS_WORKING
        worker.current_session_id = session.id
        return self._persist(worker)

    def record_session_started(
        self,
        worker: AgentWorker,
        *,
        session_id: str,
        terminal_id: Optional[str],
        worktree_path: Optional[str],
        worktree_branch: Optional[str],
    ) -> AgentWorker:
        """Update worker state once the session's checkout and terminal are active."""
        worker.status = AGENT_STATUS_WORKING
        worker.current_session_id = session_id
        if terminal_id:
            worker.terminal_id = terminal_id
        if worktree_path:
            worker.worktree_path = worktree_path
        if worktree_branch:
            worker.worktree_branch = worktree_branch
        return self._persist(worker)

    def should_use_warm_resume(self, worker: AgentWorker, target_cwd: Optional[Path]) -> bool:
        """Return True if this worker has warm context in the same checkout and is in warm mode."""
        if worker.context_mode != "warm" or worker.turns_completed <= 0:
            return False
        if not worker.worktree_path or not target_cwd:
            return False
        try:
            return Path(worker.worktree_path).resolve() == target_cwd.resolve()
        except OSError:
            return False

    def reset_context(self, agent_id: str) -> AgentWorker:
        """Mark an Agent Worker to start its next turn/session with a fresh context window."""
        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")
        worker.context_mode = "fresh"
        worker.turns_completed = 0
        worker.metadata = {**(worker.metadata or {}), "messages": [], "last_context_reset_at": _now_iso()}
        return self._persist(worker)

    def update_worker(
        self,
        agent_id: str,
        *,
        name: Optional[str] = None,
        auto_drain: Optional[bool] = None,
        context_mode: Optional[str] = None,
    ) -> AgentWorker:
        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")
        if name is not None and name.strip():
            worker.name = name.strip()
        if auto_drain is not None:
            worker.auto_drain = bool(auto_drain)
        if context_mode in ("warm", "fresh"):
            worker.context_mode = context_mode
        return self._persist(worker)

    def stop_worker(self, agent_id: str) -> AgentWorker:
        """Stop a resident Agent Worker and cancel/release any active terminal."""
        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")
        if worker.current_session_id:
            try:
                from .session_controls import cancel_session

                cancel_session(worker.current_session_id)
            except Exception:
                pass
        if worker.terminal_id:
            try:
                get_terminal_manager().destroy(worker.terminal_id)
            except Exception:
                pass
        worker.status = AGENT_STATUS_STOPPED
        worker.current_session_id = None
        worker.terminal_id = None
        return self._persist(worker)

    def delete_worker(self, agent_id: str) -> bool:
        worker = self.get(agent_id)
        if not worker:
            return False
        if worker.status in (AGENT_STATUS_WORKING, AGENT_STATUS_WAITING):
            self.stop_worker(agent_id)
        db = self._db()
        db.execute("DELETE FROM agent_workers WHERE id = ?", (agent_id,))
        db.commit()
        return True

    def on_session_status_changed(self, session: Any, status_value: str) -> Optional[AgentWorker]:
        """Synchronize the bound Agent Worker when a Session changes status."""
        meta = getattr(session, "metadata", None) or {}
        agent_id = meta.get("agent_id")
        worker = self.get(agent_id) if agent_id else self.get_by_session(session.id)
        if not worker or worker.status == AGENT_STATUS_STOPPED:
            return None

        if status_value == "running":
            worker.status = AGENT_STATUS_WORKING
            worker.current_session_id = session.id
            if getattr(session, "worktree_path", None):
                worker.worktree_path = session.worktree_path
            if getattr(session, "worktree_branch", None):
                worker.worktree_branch = session.worktree_branch
            return self._persist(worker)

        if status_value == "waiting":
            worker.status = AGENT_STATUS_WAITING
            worker.current_session_id = session.id
            return self._persist(worker)

        if status_value in ("verifying", "completed", "done", "failed", "cancelled"):
            was_current = worker.current_session_id == session.id
            if was_current:
                worker.current_session_id = None
                worker.last_session_id = session.id
                worker.status = AGENT_STATUS_IDLE
                worker.turns_completed += 1
                if worker.context_mode == "fresh":
                    worker.context_mode = "warm"
                if status_value in ("verifying", "completed", "done"):
                    worker.sessions_completed += 1
                if getattr(session, "worktree_path", None):
                    worker.worktree_path = session.worktree_path
                if getattr(session, "worktree_branch", None):
                    worker.worktree_branch = session.worktree_branch
                self._persist(worker)

                if worker.auto_drain and status_value in ("verifying", "completed", "done"):
                    self._try_auto_drain_next(worker)
            return worker

        return worker

    def _try_auto_drain_next(self, worker: AgentWorker) -> Optional[str]:
        """If auto_drain is enabled on an idle worker, pick up the oldest Staged session on its channel/project."""
        if worker.status != AGENT_STATUS_IDLE or not worker.auto_drain:
            return None
        try:
            from .session_manager import SessionStatus, get_session_manager

            sm = get_session_manager()
            staged = [
                s
                for s in sm.list_for_channel(worker.channel_id)
                if (s.status == SessionStatus.PROPOSED or str(s.status) == "proposed")
                and s.project == worker.project
            ]
            if not staged:
                return None
            staged.sort(key=lambda s: s.created_at)
            next_sess = staged[0]
            res = self.assign_session(worker.id, next_sess.id)
            return res.get("session_id")
        except Exception:
            logger.debug("Auto-drain skipped for worker %s", worker.id, exc_info=True)
            return None

    def assign_session(self, agent_id: str, session_id: str) -> dict[str, Any]:
        """Assign an existing session (e.g. Staged/proposed or verifying) to a specific Agent Worker and start it."""
        from .session_manager import get_session_manager
        from .supervised_sessions import start_supervised_session

        worker = self.get(agent_id)
        if not worker:
            raise LookupError(f"Agent worker not found: {agent_id}")
        if worker.status in (AGENT_STATUS_WORKING, AGENT_STATUS_WAITING) and worker.current_session_id != session_id:
            raise RuntimeError(f"Agent {worker.name} is currently busy with session {worker.current_session_id}")
        if worker.status == AGENT_STATUS_STOPPED:
            worker.status = AGENT_STATUS_IDLE
            self._persist(worker)

        sm = get_session_manager()
        session = sm.get(session_id)
        if not session:
            raise LookupError(f"Session not found: {session_id}")

        session.agent_provider = worker.provider
        meta = dict(session.metadata or {})
        meta["agent_id"] = worker.id
        meta["agent_name"] = worker.name
        session.metadata = meta

        if (
            worker.worktree_path
            and worker.project == session.project
            and not session.worktree_path
            and (session.worktree_mode or "isolated") in ("isolated", "reuse")
            and Path(worker.worktree_path).is_dir()
        ):
            record = get_project_repo().get(session.project)
            proj_root = Path(record.path).resolve() if record and record.path else None
            if proj_root is None or Path(worker.worktree_path).resolve() != proj_root:
                session.worktree_mode = "reuse"
                session.worktree_path = worker.worktree_path
                session.worktree_branch = worker.worktree_branch

        sm._persist(session)
        return start_supervised_session(session.id, provider=worker.provider)


_herd_manager: Optional[AgentHerdManager] = None


def get_agent_herd_manager() -> AgentHerdManager:
    global _herd_manager
    if _herd_manager is None:
        _herd_manager = AgentHerdManager()
    return _herd_manager
