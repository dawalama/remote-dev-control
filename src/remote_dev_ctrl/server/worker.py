"""Task Worker - Long-running process that executes tasks.

This worker polls the database for pending tasks, spawns agent subprocesses,
monitors their completion, and updates task status. It's designed to survive
API restarts and recover orphaned tasks.

Usage:
    rdc worker start       # Start worker (foreground)
    rdc worker start -d    # Start worker (daemon)
    rdc worker stop        # Stop worker gracefully
    rdc worker status      # Check worker status
"""

import asyncio
import logging
import os
import signal
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from io import TextIOWrapper
from pathlib import Path
from typing import Optional

from .config import get_rdc_home, Config
from .db.connection import get_db, init_databases
from .db.models import Task, TaskStatus, TaskPriority, Worker, WorkerStatus

logger = logging.getLogger(__name__)


class TaskWorker:
    """Worker process that executes tasks via agent subprocesses."""
    
    HEARTBEAT_INTERVAL = 10  # seconds
    POLL_INTERVAL = 2  # seconds
    MONITOR_INTERVAL = 1  # seconds
    STALE_THRESHOLD = 60  # seconds before a worker is considered dead
    
    def __init__(self, worker_id: Optional[str] = None, max_concurrent: int = 3):
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.worker_id = worker_id or f"worker-{self.hostname}"
        self.max_concurrent = max_concurrent

        self._shutdown_event = asyncio.Event()
        self._running_tasks: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, TextIOWrapper] = {}
        self._web_tasks: dict[str, asyncio.Task] = {}  # web-native async tasks
        self._web_providers: dict[str, "WebNativeProvider"] = {}
        self._config: Optional[Config] = None
    
    @property
    def db(self):
        return get_db("tasks")
    
    @property
    def logs_db(self):
        return get_db("logs")
    
    async def run(self):
        """Main worker loop."""
        init_databases()
        self._config = Config.load()
        
        self._setup_signals()
        self._register()
        self._recover_orphans()
        
        logger.info(f"Worker {self.worker_id} started (max_concurrent={self.max_concurrent})")
        
        try:
            await asyncio.gather(
                self._poll_loop(),
                self._monitor_loop(),
                self._heartbeat_loop(),
            )
        except asyncio.CancelledError:
            logger.info("Worker cancelled")
        finally:
            await self._shutdown()
    
    def _setup_signals(self):
        """Setup signal handlers for graceful shutdown."""
        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            self._shutdown_event.set()
        
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
    
    def _register(self):
        """Register this worker, cleaning up old stopped entries from this host."""
        self.logs_db.execute(
            "DELETE FROM workers WHERE hostname = ? AND status = 'stopped'",
            (self.hostname,),
        )
        now = datetime.now().isoformat()
        self.logs_db.execute("""
            INSERT OR REPLACE INTO workers (id, hostname, pid, started_at, last_heartbeat, status, max_concurrent)
            VALUES (?, ?, ?, ?, ?, 'running', ?)
        """, (self.worker_id, self.hostname, self.pid, now, now, self.max_concurrent))
        self.logs_db.commit()
        logger.info(f"Registered worker: {self.worker_id}")
    
    def _deregister(self):
        """Mark this worker as stopped."""
        self.logs_db.execute("""
            UPDATE workers SET status = 'stopped', last_heartbeat = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), self.worker_id))
        self.logs_db.commit()
        logger.info(f"Deregistered worker: {self.worker_id}")
    
    def _recover_orphans(self):
        """Recover tasks orphaned by dead workers."""
        cutoff = (datetime.now() - timedelta(seconds=self.STALE_THRESHOLD)).isoformat()
        
        dead_workers = self.logs_db.execute("""
            SELECT id FROM workers 
            WHERE status = 'running' AND last_heartbeat < ?
        """, (cutoff,)).fetchall()
        
        for row in dead_workers:
            dead_id = row[0]
            logger.warning(f"Found dead worker: {dead_id}")
            
            self.logs_db.execute("""
                UPDATE workers SET status = 'dead' WHERE id = ?
            """, (dead_id,))
            
            orphaned = self.db.execute("""
                SELECT id, agent_pid, agent_log_path FROM tasks
                WHERE claimed_by = ? AND status = 'in_progress'
            """, (dead_id,)).fetchall()
            
            for task_row in orphaned:
                task_id, agent_pid, log_path = task_row
                
                if agent_pid and self._is_process_running(agent_pid):
                    logger.info(f"Re-attaching to running agent for task {task_id} (pid={agent_pid})")
                    self.db.execute("""
                        UPDATE tasks SET claimed_by = ?, claimed_at = ?
                        WHERE id = ?
                    """, (self.worker_id, datetime.now().isoformat(), task_id))
                elif agent_pid:
                    logger.info(f"Agent for task {task_id} is gone, checking result...")
                    self._handle_orphaned_completion(task_id, log_path)
                else:
                    logger.info(f"Resetting orphaned task {task_id} to pending")
                    self.db.execute("""
                        UPDATE tasks 
                        SET status = 'pending', claimed_by = NULL, claimed_at = NULL
                        WHERE id = ?
                    """, (task_id,))
            
            self.db.commit()
        
        self.logs_db.commit()
        self._recover_running_agents()
    
    def _recover_running_agents(self):
        """Re-attach to agents that are still running from a previous session."""
        my_tasks = self.db.execute("""
            SELECT id, agent_pid, agent_log_path FROM tasks
            WHERE claimed_by = ? AND status = 'in_progress' AND agent_pid IS NOT NULL
        """, (self.worker_id,)).fetchall()
        
        for row in my_tasks:
            task_id, agent_pid, log_path = row
            if self._is_process_running(agent_pid):
                logger.info(f"Re-attaching to agent for task {task_id} (pid={agent_pid})")
                self._running_tasks[task_id] = _PidWatcher(agent_pid)
            else:
                self._handle_orphaned_completion(task_id, log_path)
    
    def _handle_orphaned_completion(self, task_id: str, log_path: Optional[str]):
        """Handle a task whose agent completed while no worker was watching."""
        exit_code = None
        output = None
        
        if log_path and Path(log_path).exists():
            output, exit_code = self._parse_log_for_result(log_path)
        
        now = datetime.now().isoformat()
        if exit_code == 0:
            self.db.execute("""
                UPDATE tasks 
                SET status = 'completed', completed_at = ?, result = ?
                WHERE id = ?
            """, (now, output or "Completed (recovered)", task_id))
        else:
            error = f"Exit code {exit_code}" if exit_code is not None else "Agent exited while unmonitored — check logs"
            self.db.execute("""
                UPDATE tasks 
                SET status = 'failed', completed_at = ?, error = ?, output = ?
                WHERE id = ?
            """, (now, error, output, task_id))
        
        self.db.commit()
        logger.info(f"Recovered orphaned task {task_id}: exit_code={exit_code}")
    
    def _parse_log_for_result(self, log_path: str) -> tuple[Optional[str], Optional[int]]:
        """Parse agent log file for exit code and output."""
        import re
        try:
            content = Path(log_path).read_text()
            lines = content.strip().split("\n")
            
            for line in reversed(lines[-20:]):
                if "exited with code" in line.lower():
                    match = re.search(r'code\s+(\d+)', line)
                    if match:
                        exit_code = int(match.group(1))
                        output = "\n".join(lines[-50:-1])
                        return output, exit_code
            
            return "\n".join(lines[-50:]), None
        except Exception as e:
            logger.warning(f"Failed to parse log {log_path}: {e}")
            return None, None
    
    # =========================================================================
    # MAIN LOOPS (use _shutdown_event for responsive exit)
    # =========================================================================
    
    async def _wait_or_shutdown(self, timeout: float) -> bool:
        """Wait for timeout or shutdown signal. Returns True if shutdown requested."""
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
    
    async def _poll_loop(self):
        """Poll for new tasks to execute."""
        while not self._shutdown_event.is_set():
            try:
                if len(self._running_tasks) < self.max_concurrent:
                    task = self._claim_next_task()
                    if task:
                        self._spawn_agent(task)
                
                self.logs_db.execute("""
                    UPDATE workers SET current_load = ? WHERE id = ?
                """, (len(self._running_tasks), self.worker_id))
                self.logs_db.commit()
                
            except Exception as e:
                logger.error(f"Poll error: {e}")
            
            if await self._wait_or_shutdown(self.POLL_INTERVAL):
                return
    
    def _claim_next_task(self) -> Optional[dict]:
        """Atomically claim the next pending task."""
        now = datetime.now().isoformat()
        
        cursor = self.db.execute("""
            UPDATE tasks
            SET claimed_by = ?, claimed_at = ?, status = 'in_progress', started_at = ?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'pending' AND claimed_by IS NULL
                ORDER BY
                    CASE priority
                        WHEN 'urgent' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2
                        WHEN 'low' THEN 3
                    END,
                    created_at ASC
                LIMIT 1
            )
            RETURNING id, project_id, description, priority, metadata
        """, (self.worker_id, now, now))

        row = cursor.fetchone()
        self.db.commit()

        if row:
            project_id = row[1]
            # Resolve project_id (UUID) to project name and path
            proj_row = get_db("rdc").execute(
                "SELECT name, path FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            project_name = proj_row[0] if proj_row else None
            project_path = proj_row[1] if proj_row else None
            # Parse metadata JSON for model etc.
            import json as _json
            meta_raw = row[4]
            meta = _json.loads(meta_raw) if meta_raw else {}
            return {
                "id": row[0],
                "project_id": project_id,
                "project": project_name,
                "project_path": project_path,
                "description": row[2],
                "priority": row[3],
                "model": meta.get("model"),
                "provider": meta.get("provider"),
            }
        return None
    
    def _spawn_agent(self, task: dict):
        """Spawn an agent subprocess or web-native task."""
        task_id = task["id"]
        project_id = task["project_id"]
        project = task["project"]
        project_path = task["project_path"]
        description = task["description"]

        if not project or not project_path:
            logger.error(f"Project not found for project_id: {project_id}")
            self._fail_task(task_id, f"Project not found for project_id: {project_id}")
            return

        # Check if this should use a native provider
        task_provider = task.get("provider")
        task_model = task.get("model") or ""

        # Auto-route ollama models to web-native provider
        if not task_provider and task_model.startswith("ollama/"):
            task_provider = "web"

        # Default provider is now "gwd" (native task executor)
        if not task_provider:
            task_provider = (self._config.agents.default_provider if self._config else "gwd")

        if task_provider == "builtin":
            self._spawn_builtin_task(task)
            return

        if task_provider == "gwd":
            self._spawn_gwd_agent(task)
            return

        if task_provider == "web":
            self._spawn_web_agent(task)
            return

        log_dir = get_rdc_home() / "logs" / "agents"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{project}-{task_id[:8]}.log"

        provider = task_provider or (self._config.agents.default_provider if self._config else "cursor")
        model = task.get("model")

        # cursor-agent only accepts its own model names — strip OpenRouter-style
        # model IDs (e.g. "inception/mercury-2") that would cause a 404.
        if provider in ("cursor", "cursor-agent") and model and "/" in model:
            logger.info("Skipping unsupported model %r for cursor-agent, using default", model)
            model = None

        if provider in ("cursor", "cursor-agent"):
            cmd = ["cursor-agent", "-p", description]
            if model:
                cmd.extend(["--model", model])
        else:
            cmd = [
                "python", "-m", "remote_dev_ctrl.server.agents.runner",
                "--project", project,
                "--provider", provider,
                "--task", description,
            ]
            if model:
                cmd.extend(["--model", model])

        log_file = None
        try:
            log_file = open(log_path, "a")
            log_file.write(f"\n=== Task {task_id} started at {datetime.now().isoformat()} ===\n")
            log_file.write(f"Project: {project} (id={project_id})\n")
            log_file.write(f"Description: {description[:200]}\n")
            log_file.write("=" * 50 + "\n\n")
            log_file.flush()

            process = subprocess.Popen(
                cmd,
                cwd=project_path,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

            self._running_tasks[task_id] = process
            self._log_files[task_id] = log_file

            self.db.execute("""
                UPDATE tasks SET agent_pid = ?, agent_log_path = ?
                WHERE id = ?
            """, (process.pid, str(log_path), task_id))
            self.db.commit()

            self.logs_db.execute("""
                INSERT INTO events (type, project_id, task_id, message, data)
                VALUES ('task.started', ?, ?, ?, ?)
            """, (project_id, task_id, f"Agent spawned (pid={process.pid})",
                  f'{{"worker_id": "{self.worker_id}", "pid": {process.pid}}}'))
            self.logs_db.commit()

            logger.info(f"Spawned agent for task {task_id} (pid={process.pid})")

        except Exception as e:
            if log_file:
                log_file.close()
            logger.error(f"Failed to spawn agent for task {task_id}: {e}")
            self._fail_task(task_id, str(e))
    
    def _close_log_file(self, task_id: str):
        """Close and remove the log file handle for a task."""
        log_file = self._log_files.pop(task_id, None)
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass
    
    def _fail_task(self, task_id: str, error: str):
        """Mark a task as failed."""
        now = datetime.now().isoformat()
        self.db.execute("""
            UPDATE tasks
            SET status = 'failed', completed_at = ?, error = ?
            WHERE id = ?
        """, (now, error, task_id))
        self.db.commit()

        self._running_tasks.pop(task_id, None)
        self._web_tasks.pop(task_id, None)
        self._web_providers.pop(task_id, None)
        self._close_log_file(task_id)

    def _spawn_web_agent(self, task: dict):
        """Launch a web-native agent as an async task."""
        from .agents.web_provider import WebNativeProvider
        from .streaming import get_stream_manager

        task_id = task["id"]
        project = task["project"]
        project_path = task["project_path"]
        description = task["description"]
        model = task.get("model")

        provider = WebNativeProvider()
        self._web_providers[task_id] = provider

        stream_manager = get_stream_manager()

        async def on_step(step):
            await stream_manager.emit_task_step(task_id, step.to_dict())

        async def run_agent():
            try:
                result = await provider.run(
                    task_description=description,
                    project_path=project_path,
                    project_name=project,
                    model=model,
                    on_step=on_step,
                )
                # Mark completed
                now = datetime.now().isoformat()
                self.db.execute("""
                    UPDATE tasks
                    SET status = 'completed', completed_at = ?, result = ?, output = ?
                    WHERE id = ?
                """, (now, "Success", result, task_id))
                self.db.commit()
                logger.info(f"Web agent task {task_id} completed")
            except Exception as e:
                logger.error(f"Web agent task {task_id} failed: {e}")
                self._fail_task(task_id, str(e))
            finally:
                self._web_tasks.pop(task_id, None)
                self._web_providers.pop(task_id, None)

        # Use a _WebTaskWatcher so monitor_loop can track it
        watcher = _WebTaskWatcher()
        self._running_tasks[task_id] = watcher
        async_task = asyncio.create_task(run_agent())
        self._web_tasks[task_id] = async_task

        # When the async task finishes, mark watcher as done
        def on_done(fut):
            watcher.mark_done(0 if not fut.exception() else 1)
        async_task.add_done_callback(on_done)

        logger.info(f"Spawned web-native agent for task {task_id}")

    def _spawn_gwd_agent(self, task: dict):
        """Launch a gwd (get-work-done) native agent as an async task."""
        from .agents.gwd_provider import GWDProvider
        from .streaming import get_stream_manager

        task_id = task["id"]
        project = task["project"]
        project_path = task["project_path"]
        description = task["description"]
        model = task.get("model")

        provider = GWDProvider()
        self._web_providers[task_id] = provider

        stream_manager = get_stream_manager()

        async def on_step(step):
            await stream_manager.emit_task_step(task_id, step.to_dict())

        async def run_agent():
            try:
                result = await provider.run(
                    task_description=description,
                    project_path=project_path,
                    project_name=project,
                    model=model,
                    on_step=on_step,
                )
                now = datetime.now().isoformat()
                self.db.execute("""
                    UPDATE tasks
                    SET status = 'completed', completed_at = ?, result = ?, output = ?
                    WHERE id = ?
                """, (now, "Success", result, task_id))
                self.db.commit()
                logger.info(f"GWD agent task {task_id} completed")
            except Exception as e:
                logger.error(f"GWD agent task {task_id} failed: {e}")
                self._fail_task(task_id, str(e))
            finally:
                self._web_tasks.pop(task_id, None)
                self._web_providers.pop(task_id, None)

        watcher = _WebTaskWatcher()
        self._running_tasks[task_id] = watcher
        async_task = asyncio.create_task(run_agent())
        self._web_tasks[task_id] = async_task

        def on_done(fut):
            watcher.mark_done(0 if not fut.exception() else 1)
        async_task.add_done_callback(on_done)

        logger.info(f"Spawned gwd agent for task {task_id}")

    def _spawn_builtin_task(self, task: dict):
        """Run a built-in Python function as a task (no LLM needed)."""
        from .streaming import get_stream_manager

        task_id = task["id"]
        project = task["project"]
        project_path = task["project_path"]

        # Parse metadata to find which builtin to run
        import json as _json
        meta_raw = self.db.execute("SELECT metadata FROM tasks WHERE id = ?", (task_id,)).fetchone()
        meta = _json.loads(meta_raw[0]) if meta_raw and meta_raw[0] else {}
        builtin_id = meta.get("builtin_id", "")

        stream_manager = get_stream_manager()

        async def emit(msg: str):
            await stream_manager.emit_task_step(task_id, {
                "type": "text", "content": msg,
            })

        async def run_builtin():
            try:
                if builtin_id == "project_setup":
                    output = await self._builtin_project_setup(project, project_path, emit)
                else:
                    raise ValueError(f"Unknown builtin: {builtin_id}")

                now = datetime.now().isoformat()
                self.db.execute("""
                    UPDATE tasks
                    SET status = 'completed', completed_at = ?, result = ?, output = ?
                    WHERE id = ?
                """, (now, "Success", output, task_id))
                self.db.commit()
                logger.info(f"Builtin task {task_id} ({builtin_id}) completed")
            except Exception as e:
                logger.error(f"Builtin task {task_id} ({builtin_id}) failed: {e}")
                self._fail_task(task_id, str(e))
            finally:
                self._web_tasks.pop(task_id, None)

        watcher = _WebTaskWatcher()
        self._running_tasks[task_id] = watcher
        async_task = asyncio.create_task(run_builtin())
        self._web_tasks[task_id] = async_task

        def on_done(fut):
            watcher.mark_done(0 if not fut.exception() else 1)
        async_task.add_done_callback(on_done)

        logger.info(f"Spawned builtin task {task_id} ({builtin_id})")

    async def _builtin_project_setup(self, project_name: str, project_path: str, emit) -> str:
        """Run project setup: stack detection, profile save, process discovery."""
        from .process_discovery import detect_stack, discover_processes
        from .db.repositories import get_process_config_repo, get_project_repo, resolve_project_id
        from .db.models import ProcessConfig
        from .ports import get_port_manager
        from .processes import get_process_manager

        lines = []

        # Step 1: Detect stack
        await emit("Detecting project stack...")
        try:
            stack_info = await asyncio.to_thread(detect_stack, project_path)
            stack = stack_info.get("stack", [])
            lines.append(f"Stack: {', '.join(stack) if stack else 'none detected'}")
            if stack_info.get("test_command"):
                lines.append(f"Test command: {stack_info['test_command']}")
            await emit(lines[-1] if lines else "Stack detection done")
        except Exception as e:
            lines.append(f"Stack detection failed: {e}")
            await emit(lines[-1])
            stack_info = {}

        # Step 2: Save profile to DB
        await emit("Saving project profile...")
        try:
            repo = get_project_repo()
            db_proj = repo.get(project_name)
            if db_proj:
                existing_config = db_proj.config or {}
                existing_config["profile"] = {
                    "stack": stack_info.get("stack", []),
                    "test_command": stack_info.get("test_command"),
                    "source_dir": stack_info.get("source_dir"),
                    "test_dir": stack_info.get("test_dir"),
                }
                db_proj.config = existing_config
                repo.update(db_proj)
                lines.append("Profile saved")
        except Exception as e:
            lines.append(f"Profile save failed: {e}")
        await emit(lines[-1])

        # Step 3: Discover processes
        await emit("Discovering processes...")
        try:
            proj_uuid = resolve_project_id(project_name) or ""
            discovered = await asyncio.to_thread(discover_processes, project_name, project_path, True)
            if discovered:
                port_manager = get_port_manager()
                process_config_repo = get_process_config_repo()
                process_manager = get_process_manager()
                for proc in discovered:
                    process_config_repo.upsert(ProcessConfig(
                        id=f"{project_name}-{proc.name}",
                        project_id=proj_uuid,
                        name=proc.name,
                        command=proc.command,
                        cwd=proc.cwd,
                        port=proc.default_port,
                        description=proc.description,
                        discovered_by="setup",
                    ))
                    cwd = str(Path(project_path) / proc.cwd) if proc.cwd else project_path
                    port = None
                    cmd = proc.command
                    if proc.default_port:
                        port = port_manager.assign_port(project_name, proc.name, preferred=proc.default_port)
                        cmd = process_manager._adjust_command_port(proc.command, port)
                    process_manager.register(
                        project=project_name, name=proc.name,
                        command=cmd, cwd=cwd, port=port, force_update=True,
                    )
                lines.append(f"Discovered {len(discovered)} process(es)")
                for proc in discovered:
                    lines.append(f"  - {proc.name}: {proc.command}")
            else:
                lines.append("No processes discovered")
            await emit(lines[-1])
        except Exception as e:
            lines.append(f"Process discovery failed: {e}")
            await emit(lines[-1])

        await emit("Setup complete")
        return "\n".join(lines)

    async def _monitor_loop(self):
        """Monitor running agents for completion and timeouts."""
        while not self._shutdown_event.is_set():
            try:
                for task_id, process in list(self._running_tasks.items()):
                    exit_code = process.poll()
                    if exit_code is not None:
                        self._handle_completion(task_id, exit_code)
                        del self._running_tasks[task_id]
                    else:
                        self._check_timeout(task_id, process)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
            
            if await self._wait_or_shutdown(self.MONITOR_INTERVAL):
                return
    
    def _check_timeout(self, task_id: str, process):
        """Kill agent if it has exceeded its timeout."""
        row = self.db.execute("""
            SELECT started_at, timeout_seconds FROM tasks WHERE id = ?
        """, (task_id,)).fetchone()
        
        if not row or not row[0]:
            return
        
        started_at = datetime.fromisoformat(row[0])
        timeout = row[1] or 3600
        elapsed = (datetime.now() - started_at).total_seconds()
        
        if elapsed > timeout:
            logger.warning(f"Task {task_id} exceeded timeout ({timeout}s), killing agent")
            try:
                process.kill()
            except OSError:
                pass
            self._fail_task(task_id, f"Timed out after {int(elapsed)}s (limit: {timeout}s)")
    
    def _handle_completion(self, task_id: str, exit_code: int):
        """Handle agent completion."""
        now = datetime.now().isoformat()

        self._close_log_file(task_id)

        row = self.db.execute("""
            SELECT project_id, agent_log_path, status FROM tasks WHERE id = ?
        """, (task_id,)).fetchone()

        if not row:
            logger.error(f"Task {task_id} not found during completion")
            return

        project_id, log_path, current_status = row

        # Web-native tasks already wrote status/output in run_agent() — skip DB update
        # to avoid overwriting the output with None.
        is_web_task = task_id in self._web_tasks or current_status in ("completed", "failed")

        if not is_web_task:
            output = None
            if log_path:
                output, _ = self._parse_log_for_result(log_path)

            if exit_code == 0:
                self.db.execute("""
                    UPDATE tasks
                    SET status = 'completed', completed_at = ?, result = ?, output = ?
                    WHERE id = ?
                """, (now, "Success", output, task_id))
                logger.info(f"Task {task_id} completed successfully")
            else:
                error_msg = f"Agent exited with code {exit_code}"
                self.db.execute("""
                    UPDATE tasks
                    SET status = 'failed', completed_at = ?, error = ?, output = ?
                    WHERE id = ?
                """, (now, error_msg, output, task_id))
                logger.warning(f"Task {task_id} failed: {error_msg}")

            self.db.commit()

        event_type = "task.completed" if exit_code == 0 else "task.failed"
        self.logs_db.execute("""
            INSERT INTO events (type, project_id, task_id, message)
            VALUES (?, ?, ?, ?)
        """, (event_type, project_id, task_id, f"Exit code: {exit_code}"))
        self.logs_db.commit()
    
    async def _heartbeat_loop(self):
        """Update heartbeat timestamp periodically."""
        while not self._shutdown_event.is_set():
            try:
                self.logs_db.execute("""
                    UPDATE workers SET last_heartbeat = ?, current_load = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), len(self._running_tasks), self.worker_id))
                self.logs_db.commit()
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
            
            if await self._wait_or_shutdown(self.HEARTBEAT_INTERVAL):
                return
    
    async def _shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down worker...")
        self._shutdown_event.set()
        
        if self._running_tasks:
            logger.info(f"Waiting for {len(self._running_tasks)} running tasks...")
            wait_start = datetime.now()
            while self._running_tasks and (datetime.now() - wait_start).seconds < 30:
                for task_id, process in list(self._running_tasks.items()):
                    exit_code = process.poll()
                    if exit_code is not None:
                        self._handle_completion(task_id, exit_code)
                        del self._running_tasks[task_id]
                await asyncio.sleep(1)
            
            if self._running_tasks:
                logger.warning(f"{len(self._running_tasks)} tasks still running, leaving for next worker")
        
        for task_id in list(self._log_files):
            self._close_log_file(task_id)
        
        self._deregister()
        logger.info("Worker shutdown complete")
    
    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is running."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class _PidWatcher:
    """Wrapper to watch a PID we don't have a Popen for."""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self) -> Optional[int]:
        """Check if process is still running. Returns None if running, -1 if gone."""
        try:
            os.kill(self.pid, 0)
            return None
        except OSError:
            return -1  # Unknown exit — treated as failure


class _WebTaskWatcher:
    """Wrapper so web-native async tasks can be tracked by _monitor_loop."""

    def __init__(self):
        self.pid = None  # No actual PID
        self._exit_code: Optional[int] = None

    def poll(self) -> Optional[int]:
        return self._exit_code

    def mark_done(self, exit_code: int = 0):
        self._exit_code = exit_code


# Global reference so the WS endpoint can find web providers for approval relay
_active_worker: Optional[TaskWorker] = None


def run_worker(max_concurrent: int = 3, worker_id: Optional[str] = None):
    """Entry point for running the worker."""
    global _active_worker
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    worker = TaskWorker(worker_id=worker_id, max_concurrent=max_concurrent)
    _active_worker = worker
    asyncio.run(worker.run())


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RDC Task Worker")
    parser.add_argument("--id", dest="worker_id", help="Worker ID")
    parser.add_argument("--max", dest="max_concurrent", type=int,
                        default=int(os.environ.get("RDC_WORKER_MAX_CONCURRENT", os.environ.get("ADT_WORKER_MAX_CONCURRENT", "3"))),
                        help="Max concurrent tasks")
    args = parser.parse_args()
    
    run_worker(max_concurrent=args.max_concurrent, worker_id=args.worker_id)
