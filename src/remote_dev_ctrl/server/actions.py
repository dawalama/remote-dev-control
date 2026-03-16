"""Action manager — routes execute/stop by action kind (service vs command)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from datetime import datetime
from typing import Optional

from .db.models import ActionKind, ProcessConfig, ProcessStatus
from .processes import ProcessManager

logger = logging.getLogger(__name__)


class ActionManager:
    """Thin layer over ProcessManager that understands action kinds.

    * **service** — delegated to ProcessManager (start/stop/restart)
    * **command** — one-shot subprocess, monitored to completion
    """

    def __init__(self, process_manager: ProcessManager):
        self._pm = process_manager
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, action_id: str) -> ProcessConfig:
        """Execute an action by kind."""
        state = self._pm.get(action_id)
        if not state:
            raise ValueError(f"Action not found: {action_id}")

        if state.kind == ActionKind.COMMAND:
            return self._run_command(state)
        else:
            # Default: service
            return self._pm.start(action_id)

    def stop(self, action_id: str, force: bool = False) -> ProcessConfig:
        """Stop any kind of action."""
        return self._pm.stop(action_id, force=force)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _run_command(self, state: ProcessConfig) -> ProcessConfig:
        """Run a one-shot command to completion.

        Uses a lock to prevent two concurrent requests from launching the
        same command twice (TOCTOU guard on the status check).
        """
        with self._lock:
            # Re-read state inside lock to avoid TOCTOU race
            state = self._pm.get(state.id)
            if not state:
                raise ValueError(f"Action vanished: {state}")
            if state.status == ProcessStatus.RUNNING:
                raise ValueError(f"Command already running: {state.id}")

            # Mark as running immediately while holding the lock
            state.status = ProcessStatus.RUNNING
            state.started_at = datetime.now()
            state.completed_at = None
            state.exit_code = None
            state.error = None
            self._pm._repo.upsert(state)
            self._pm._processes[state.id] = state

        # Spawn outside lock — Popen can block briefly
        state.log_path().parent.mkdir(parents=True, exist_ok=True)
        log_file = open(state.log_path(), "a")
        log_file.write(f"\n=== Command started at {datetime.now().isoformat()} ===\n")
        log_file.write(f"Command: {state.command}\n{'=' * 50}\n\n")
        log_file.flush()

        env = os.environ.copy()
        env["FORCE_COLOR"] = "1"

        try:
            process = subprocess.Popen(
                state.command,
                shell=True,
                cwd=state.cwd or None,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            log_file.close()
            state.status = ProcessStatus.FAILED
            state.error = str(e)
            state.pid = None
            self._pm._repo.upsert(state)
            raise

        state.pid = process.pid
        self._pm._repo.upsert(state)
        self._pm._subprocesses[state.id] = process

        # Monitor thread — marks COMPLETED / FAILED on exit
        t = threading.Thread(
            target=self._monitor_command,
            args=(state.id, process, log_file),
            daemon=True,
        )
        t.start()

        return state

    def _monitor_command(
        self,
        action_id: str,
        process: subprocess.Popen,
        log_file,
    ) -> None:
        """Wait for a command to finish, then update state."""
        exit_code = process.wait()
        now = datetime.now()

        try:
            log_file.write(f"\n=== Command exited with code {exit_code} at {now.isoformat()} ===\n")
            log_file.close()
        except Exception:
            pass

        self._pm._subprocesses.pop(action_id, None)

        state = self._pm._processes.get(action_id)
        if not state:
            return

        state.exit_code = exit_code
        state.pid = None
        state.completed_at = now

        if exit_code == 0:
            state.status = ProcessStatus.COMPLETED
            state.error = None
        else:
            state.status = ProcessStatus.FAILED
            state.error = self._pm._extract_error(action_id)

        self._pm._repo.upsert(state)
        self._pm._emit("exited", action_id, exit_code, state)

        # Trigger state broadcast — thread-safe call into the asyncio loop
        try:
            from .state_machine import get_state_machine
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(
                get_state_machine()._broadcast_state(), loop
            )
        except RuntimeError:
            pass  # No event loop running (e.g. during shutdown)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_action_manager: Optional[ActionManager] = None


def get_action_manager() -> ActionManager:
    global _action_manager
    if _action_manager is None:
        from .processes import get_process_manager
        _action_manager = ActionManager(get_process_manager())
    return _action_manager
