"""Process management for long-running project services (dev servers, etc.)."""

from __future__ import annotations

import logging
import subprocess
import signal
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

from .db.models import ActionKind, ProcessConfig, ProcessStatus, ProcessType


# Common dev server commands by stack
DEV_COMMANDS = {
    "react": "npm run dev",
    "vite": "npm run dev",
    "next": "npm run dev",
    "vue": "npm run dev",
    "express": "npm run dev",
    "fastapi": "uvicorn main:app --reload",
    "django": "python manage.py runserver",
    "flask": "flask run --reload",
}


def detect_dev_command(project_path: str, assigned_port: Optional[int] = None) -> Optional[tuple[str, int]]:
    """Detect the appropriate dev command for a project.

    Returns (command, port) or None.
    If assigned_port is given, the command will use that port.
    """
    path = Path(project_path)

    # Check package.json for Node.js projects
    pkg_json = path / "package.json"
    if pkg_json.exists():
        import json
        try:
            pkg = json.loads(pkg_json.read_text())
            scripts = pkg.get("scripts", {})

            if "dev" in scripts:
                # Try to detect the actual tool from the dev script
                dev_script = scripts["dev"]
                port = 3000  # default

                # Detect port from script
                if "--port" in dev_script:
                    parts = dev_script.split("--port")
                    if len(parts) > 1:
                        port_str = parts[1].strip().split()[0]
                        try:
                            port = int(port_str)
                        except ValueError:
                            pass
                elif "5173" in dev_script:  # Vite default
                    port = 5173

                # Return direct tool command to avoid npm wrapper (orphan processes)
                deps = pkg.get("devDependencies", {})
                deps.update(pkg.get("dependencies", {}))

                if "vite" in dev_script or "vite" in deps:
                    # --host allows access from Docker containers for VNC preview
                    return ("npx vite --host", port if port != 3000 else 5173)
                elif "next" in dev_script or "next" in deps:
                    return ("npx next dev", 3000)
                elif "react-scripts" in dev_script or "react-scripts" in deps:
                    return ("npx react-scripts start", 3000)
                elif "vue" in dev_script:
                    return ("npx vue-cli-service serve", 8080)
                else:
                    # Fallback to npm run dev if we can't detect the tool
                    return ("npm run dev", port)
            elif "start" in scripts:
                return ("npm start", 3000)
        except json.JSONDecodeError:
            pass

    # Check for Python projects
    if (path / "main.py").exists() or (path / "app.py").exists():
        if (path / "requirements.txt").exists():
            reqs = (path / "requirements.txt").read_text().lower()
            if "fastapi" in reqs or "uvicorn" in reqs:
                main_file = "main" if (path / "main.py").exists() else "app"
                return (f"uvicorn {main_file}:app --reload --port 8000", 8000)
            elif "flask" in reqs:
                return ("flask run --reload --port 5000", 5000)
            elif "django" in reqs:
                return ("python manage.py runserver 8000", 8000)

    # Check for pyproject.toml
    if (path / "pyproject.toml").exists():
        toml_content = (path / "pyproject.toml").read_text().lower()
        if "fastapi" in toml_content:
            return ("uvicorn main:app --reload --port 8000", 8000)

    return None


class ProcessManager:
    """Manages long-running processes for projects."""

    def __init__(self):
        from .db.repositories import get_process_config_repo
        self._repo = get_process_config_repo()

        self._processes: dict[str, ProcessConfig] = {}
        self._subprocesses: dict[str, subprocess.Popen] = {}
        self._log_files: dict[str, object] = {}
        self._callbacks: dict[str, list[Callable]] = {}
        self._stopping: set[str] = set()  # Processes being intentionally stopped

        # Load existing process states from DB
        self._load_states()

    def _load_states(self):
        """Load persisted process states from the database."""
        try:
            states = self._repo.list()
        except Exception as e:
            logging.error(f"Failed to load process states from DB: {e}")
            return

        logging.info(f"Loaded {len(states)} process configs from DB")

        for state in states:
            # Check if process marked as running is actually still alive
            if state.status == ProcessStatus.RUNNING and state.pid:
                if self._is_pid_alive(state.pid):
                    # Process still running - keep track of it
                    pass
                else:
                    # PID no longer exists - mark as stopped
                    state.status = ProcessStatus.STOPPED
                    state.pid = None
                    self._repo.update_status(
                        state.id, state.status.value,
                        pid=state.pid,
                    )
            elif state.status == ProcessStatus.RUNNING:
                # Running but no PID - mark as stopped
                state.status = ProcessStatus.STOPPED
                self._repo.update_status(
                    state.id, state.status.value,
                    pid=None,
                )

            self._processes[state.id] = state

    def _is_pid_alive(self, pid: int) -> bool:
        """Check if a process with given PID is still running."""
        try:
            os.kill(pid, 0)  # Signal 0 doesn't kill, just checks existence
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we don't have permission (still alive)
            return True

    def on(self, event: str, callback: Callable):
        """Register event callback."""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    def _emit(self, event: str, *args):
        """Emit event to callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(*args)
            except Exception:
                pass

    def register(
        self,
        project: str,
        name: str,
        command: str,
        cwd: str,
        process_type: ProcessType = ProcessType.DEV_SERVER,
        port: Optional[int] = None,
        kind: ActionKind = ActionKind.SERVICE,
        force_update: bool = False,
    ) -> ProcessConfig:
        """Register a new process configuration.

        If force_update is True, updates existing config even if already registered.
        """
        from .db.repositories import resolve_project_id

        process_id = f"{project}-{name}".lower().replace(" ", "-")
        project_id = resolve_project_id(project) or ""

        # Check if already exists and not forcing update
        existing = self._processes.get(process_id)
        if existing and not force_update:
            # Update port if changed
            if port and existing.port != port:
                existing.port = port
                existing.command = command
                self._repo.upsert(existing)
            return existing

        state = ProcessConfig(
            id=process_id,
            project_id=project_id,
            project=project,
            name=name,
            kind=kind,
            process_type=process_type,
            command=command,
            cwd=cwd,
            port=port,
        )
        self._repo.upsert(state)
        self._processes[process_id] = state
        return state

    def start(self, process_id: str, force: bool = False) -> ProcessConfig:
        """Start a registered process.

        Args:
            process_id: ID of the process to start
            force: If True, kill any process using the port before starting
        """
        from .ports import get_port_manager

        state = self._processes.get(process_id)
        if not state:
            raise ValueError(f"Process not found: {process_id}")

        # Force kill any process using our port
        if force and state.port:
            self.kill_port(state.port)

        if state.status == ProcessStatus.RUNNING:
            # Check if it's actually still running
            if state.pid and self._is_pid_alive(state.pid):
                raise ValueError(f"Process already running: {process_id} (PID: {state.pid})")
            else:
                # Orphaned state - reset it
                state.status = ProcessStatus.STOPPED
                state.pid = None
                self._repo.upsert(state)

        # Check if port has been updated in the registry
        port_manager = get_port_manager()
        registered_port = port_manager.get_port(state.project, state.name)
        if registered_port and registered_port != state.port:
            # Port was changed - update the command
            old_port = state.port
            state.port = registered_port
            state.command = self._update_command_port(state.command, old_port, registered_port)
            self._repo.upsert(state)

        # Ensure log directory exists
        state.log_path().parent.mkdir(parents=True, exist_ok=True)

        # Open log file
        log_file = open(state.log_path(), "a")
        log_file.write(f"\n\n=== Process started at {datetime.now().isoformat()} ===\n")
        log_file.write(f"Command: {state.command}\n")
        log_file.write(f"CWD: {state.cwd}\n")
        log_file.write("=" * 50 + "\n\n")
        log_file.flush()

        self._log_files[process_id] = log_file

        # Start the process
        try:
            env = os.environ.copy()
            # Add common dev environment variables
            env["FORCE_COLOR"] = "1"
            env["NODE_ENV"] = "development"

            process = subprocess.Popen(
                state.command,
                shell=True,
                cwd=state.cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                preexec_fn=os.setsid,  # Create new process group
            )

            self._subprocesses[process_id] = process

            state.status = ProcessStatus.RUNNING
            state.pid = process.pid
            state.started_at = datetime.now()
            state.exit_code = None
            state.error = None
            self._repo.upsert(state)

            # Start monitoring thread
            monitor = threading.Thread(
                target=self._monitor_process,
                args=(process_id, process),
                daemon=True,
            )
            monitor.start()

            self._emit("started", process_id, state)

            # Auto-start VNC for web processes if configured
            if state.port and state.port < 10000:  # Likely a web server
                try:
                    from .vnc import get_vnc_manager
                    vnc_manager = get_vnc_manager()
                    # Check if auto-start is desired (can be configured later)
                    # For now, just make it available but don't auto-start
                    pass
                except Exception:
                    pass

        except Exception as e:
            state.status = ProcessStatus.FAILED
            state.error = str(e)
            self._repo.upsert(state)
            log_file.close()
            del self._log_files[process_id]
            raise

        return state

    def stop(self, process_id: str, force: bool = False) -> ProcessConfig:
        """Stop a running process."""
        state = self._processes.get(process_id)
        if not state:
            raise ValueError(f"Process not found: {process_id}")

        if state.status != ProcessStatus.RUNNING:
            return state

        # Mark as intentionally stopping so monitor doesn't mark as failed
        self._stopping.add(process_id)

        sig = signal.SIGKILL if force else signal.SIGTERM
        killed = False

        # Try to kill via subprocess reference first
        process = self._subprocesses.get(process_id)
        if process:
            try:
                # Kill the entire process group
                os.killpg(os.getpgid(process.pid), sig)
                killed = True
            except ProcessLookupError:
                pass
            except Exception as e:
                state.error = str(e)

        # If we have a PID stored, try killing that too
        if state.pid and not killed:
            try:
                os.killpg(os.getpgid(state.pid), sig)
                killed = True
            except ProcessLookupError:
                pass
            except Exception:
                pass

        # Last resort: find and kill any process using our port
        if state.port and not killed:
            self._kill_port_process(state.port, sig)

        state.status = ProcessStatus.STOPPED
        state.pid = None
        state.error = None  # Clear any previous error
        self._repo.upsert(state)

        self._emit("stopped", process_id, state)

        return state

    def get_port_process_info(self, port: int) -> dict | None:
        """Get information about process running on a port.

        Returns:
            Dict with pid, command, user info or None if port is free
        """
        import subprocess as sp
        try:
            # Use lsof to find process info
            result = sp.run(
                ["lsof", "-i", f":{port}", "-P", "-n"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:  # Skip header
                    # Prefer the LISTEN line (actual server) over clients/tunnels
                    data_lines = lines[1:]
                    chosen = next(
                        (l for l in data_lines if '(LISTEN)' in l),
                        data_lines[0]
                    )
                    # Parse lsof output: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
                    parts = chosen.split()
                    if len(parts) >= 3:
                        pid = int(parts[1])
                        # Get full command line
                        cmd_result = sp.run(
                            ["ps", "-p", str(pid), "-o", "args="],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        return {
                            "pid": pid,
                            "command": parts[0],
                            "full_command": cmd_result.stdout.strip() if cmd_result.returncode == 0 else parts[0],
                            "user": parts[2] if len(parts) > 2 else "unknown",
                            "port": port
                        }
            return None
        except Exception as e:
            logging.error(f"Error getting port process info: {e}")
            return None

    def attach_to_port(self, process_id: str, port: int) -> ProcessConfig | None:
        """Attach to an existing process running on a port.

        Args:
            process_id: Our internal process ID to associate with
            port: Port the process is running on

        Returns:
            Updated ProcessConfig or None if failed
        """
        state = self._processes.get(process_id)
        if not state:
            logging.warning(f"Process {process_id} not found in our records (known: {list(self._processes.keys())})")
            return None

        info = self.get_port_process_info(port)
        if not info:
            logging.warning(f"No process found listening on port {port} (lsof found nothing)")
            # Still allow attach if we have the process config — just mark it
            # as running on the given port without PID info
            state.port = port
            state.status = ProcessStatus.RUNNING
            state.started_at = datetime.now()
            self._repo.upsert(state)
            logging.info(f"Attached {process_id} to port {port} (no local PID found — may be remote)")
            self._emit("started", process_id, state)
            return state

        # Update state to track this external process
        state.pid = info["pid"]
        state.port = port
        state.status = ProcessStatus.RUNNING
        state.started_at = datetime.now()
        self._repo.upsert(state)

        logging.info(f"Attached to existing process: {process_id} -> PID {info['pid']} on port {port}")
        self._emit("started", process_id, state)

        # Note: We can't capture stdout/stderr of an already-running process,
        # but we can monitor if it's still alive
        return state

    def kill_port(self, port: int, force: bool = False) -> dict:
        """Kill any process using the given port.

        Args:
            port: Port number to free up
            force: If True, use SIGKILL instead of SIGTERM

        Returns:
            Dict with killed PIDs and success status
        """
        sig = signal.SIGKILL if force else signal.SIGTERM
        killed_pids = self._kill_port_process(port, sig)

        # Also update any of our tracked processes using this port
        for state in self._processes.values():
            if state.port == port and state.status == ProcessStatus.RUNNING:
                state.status = ProcessStatus.STOPPED
                state.pid = None
                self._repo.upsert(state)

        return {"port": port, "killed_pids": killed_pids, "success": len(killed_pids) > 0}

    def _kill_port_process(self, port: int, sig: int = signal.SIGTERM) -> list[int]:
        """Find and kill any process using the given port, including children."""
        import subprocess as sp
        killed_pids = []
        try:
            # Use lsof to find PIDs using this port
            result = sp.run(
                ["lsof", "-t", "-i", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.stdout.strip():
                pids_to_kill = []
                for pid_str in result.stdout.strip().split('\n'):
                    try:
                        pid = int(pid_str.strip())
                        pids_to_kill.append(pid)
                        # Also find child processes using pgrep
                        child_result = sp.run(
                            ["pgrep", "-P", str(pid)],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if child_result.stdout.strip():
                            for child_pid_str in child_result.stdout.strip().split('\n'):
                                try:
                                    pids_to_kill.append(int(child_pid_str.strip()))
                                except ValueError:
                                    pass
                    except ValueError:
                        pass

                # Kill all found PIDs (children first, then parents)
                for pid in reversed(pids_to_kill):
                    try:
                        os.kill(pid, sig)
                        killed_pids.append(pid)
                    except ProcessLookupError:
                        pass
        except Exception:
            pass

        return killed_pids

    def restart(self, process_id: str) -> ProcessConfig:
        """Restart a process."""
        self.stop(process_id)
        return self.start(process_id)

    def _monitor_process(self, process_id: str, process: subprocess.Popen):
        """Monitor a process and update state when it exits."""
        exit_code = process.wait()

        # Check if this was an intentional stop
        was_intentional_stop = process_id in self._stopping
        self._stopping.discard(process_id)

        # Close log file
        if process_id in self._log_files:
            try:
                log_file = self._log_files[process_id]
                log_file.write(f"\n\n=== Process exited with code {exit_code} at {datetime.now().isoformat()} ===\n")
                log_file.close()
            except Exception:
                pass
            del self._log_files[process_id]

        # Remove from subprocesses
        if process_id in self._subprocesses:
            del self._subprocesses[process_id]

        # Extract error message if failed (and not intentionally stopped)
        error_msg = None
        if exit_code != 0 and not was_intentional_stop:
            error_msg = self._extract_error(process_id)

        # Update state
        state = self._processes.get(process_id)
        if state:
            # If intentionally stopped, mark as stopped regardless of exit code
            if was_intentional_stop:
                state.status = ProcessStatus.STOPPED
                state.error = None
            else:
                state.status = ProcessStatus.FAILED if exit_code != 0 else ProcessStatus.STOPPED
                state.error = error_msg

            state.exit_code = exit_code
            state.pid = None
            self._repo.update_status(
                state.id,
                state.status.value,
                pid=state.pid,
                exit_code=state.exit_code,
                error=state.error,
            )

            self._emit("exited", process_id, exit_code, state)

    def _extract_error(self, process_id: str, lines: int = 30) -> str:
        """Extract error message from recent logs."""
        logs = self.get_logs(process_id, lines=lines)
        if not logs:
            return "Process exited with error"

        # Look for common error patterns
        error_lines = []
        for line in logs.split("\n"):
            line_lower = line.lower()
            if any(kw in line_lower for kw in ["error", "exception", "failed", "cannot", "unable", "traceback"]):
                error_lines.append(line)

        if error_lines:
            return "\n".join(error_lines[-10:])  # Last 10 error lines

        # Just return last few lines
        return "\n".join(logs.split("\n")[-10:])

    def get(self, process_id: str) -> Optional[ProcessConfig]:
        """Get a process by ID."""
        return self._processes.get(process_id)

    def remove(self, process_id: str) -> None:
        """Remove a non-running process from tracking."""
        state = self._processes.get(process_id)
        if state and state.status != ProcessStatus.RUNNING:
            self._processes.pop(process_id, None)

    def list(self, project: Optional[str] = None) -> list[ProcessConfig]:
        """List all processes, optionally filtered by project."""
        processes = list(self._processes.values())
        if project:
            processes = [p for p in processes if p.project == project]
        return processes

    def list_running(self) -> list[ProcessConfig]:
        """List only running processes."""
        return [p for p in self._processes.values() if p.status == ProcessStatus.RUNNING]

    def get_logs(self, process_id: str, lines: int = 100) -> str:
        """Get recent log lines for a process."""
        state = self._processes.get(process_id)
        if not state:
            return ""

        log_path = state.log_path()
        if not log_path.exists():
            return ""

        content = log_path.read_text()
        log_lines = content.split("\n")
        return "\n".join(log_lines[-lines:])

    def auto_detect(self, project: str, project_path: str) -> list[ProcessConfig]:
        """Auto-detect and register dev processes for a project.

        Uses PortManager to assign non-conflicting ports.
        """
        from .ports import get_port_manager

        detected = []
        path = Path(project_path)
        port_manager = get_port_manager()

        # Detect services first, then assign ports
        services_to_register = []

        # Check for frontend
        frontend_dirs = ["frontend", "client", "web", "ui"]
        for frontend_dir in frontend_dirs:
            frontend_path = path / frontend_dir
            if frontend_path.exists():
                result = detect_dev_command(str(frontend_path))
                if result:
                    base_cmd, default_port = result
                    services_to_register.append({
                        "name": "frontend",
                        "base_cmd": base_cmd,
                        "cwd": str(frontend_path),
                        "default_port": default_port,
                    })
                break

        # Check for backend
        backend_dirs = ["backend", "server", "api"]
        for backend_dir in backend_dirs:
            backend_path = path / backend_dir
            if backend_path.exists():
                result = detect_dev_command(str(backend_path))
                if result:
                    base_cmd, default_port = result
                    services_to_register.append({
                        "name": "backend",
                        "base_cmd": base_cmd,
                        "cwd": str(backend_path),
                        "default_port": default_port,
                    })
                break

        # Check root for single-app projects
        if not services_to_register:
            result = detect_dev_command(str(path))
            if result:
                base_cmd, default_port = result
                services_to_register.append({
                    "name": "app",
                    "base_cmd": base_cmd,
                    "cwd": str(path),
                    "default_port": default_port,
                })

        # Assign ports and register
        for svc in services_to_register:
            # Get or assign port
            port = port_manager.assign_port(
                project=project,
                service=svc["name"],
                preferred=svc["default_port"],
            )

            # Adjust command to use assigned port
            cmd = self._adjust_command_port(svc["base_cmd"], port)

            state = self.register(
                project=project,
                name=svc["name"],
                command=cmd,
                cwd=svc["cwd"],
                port=port,
            )
            detected.append(state)

        return detected

    def _adjust_command_port(self, cmd: str, port: int) -> str:
        """Adjust a dev command to use a specific port.

        Priority:
        1. ${PORT} placeholder - explicit, user-defined (from rdc.yml)
        2. Heuristics - fallback for discovered/imported projects
        """
        import re

        # First priority: explicit ${PORT} placeholder
        if "${PORT}" in cmd:
            return cmd.replace("${PORT}", str(port))

        # Strip any existing PORT= env prefixes to avoid stacking
        cmd = re.sub(r'^(PORT=\d+\s*)+', '', cmd).strip()

        # Fallback: heuristics for discovered commands
        # (kept simple - for imported projects without rdc.yml)

        # npm/pnpm wrappers - convert to direct invocation
        if cmd in ("npm run dev", "pnpm dev"):
            return f"npx vite --port {port} --host"

        if cmd in ("npm start", "pnpm start"):
            return f"PORT={port} {cmd}"

        # Direct tool invocations
        if "uvicorn" in cmd:
            cmd = re.sub(r'--port\s*\d+', '', cmd).strip()
            return f"{cmd} --port {port}"

        if "vite" in cmd:
            cmd = re.sub(r'--port\s*\d+', '', cmd).strip()
            return f"{cmd} --port {port}"

        if "next" in cmd:
            cmd = re.sub(r'-p\s*\d+', '', cmd).strip()
            return f"{cmd} -p {port}"

        if "flask" in cmd:
            cmd = re.sub(r'--port\s*\d+', '', cmd).strip()
            return f"{cmd} --port {port}"

        if "runserver" in cmd:
            cmd = re.sub(r'runserver\s*\d*', 'runserver', cmd).strip()
            return f"{cmd} {port}"

        # Generic fallback: PORT env var
        return f"PORT={port} {cmd}"

    def _update_command_port(self, cmd: str, old_port: Optional[int], new_port: int) -> str:
        """Update port in an existing command."""
        import re

        if old_port:
            # Replace old port with new port in the command
            cmd = re.sub(rf'--port\s*{old_port}\b', f'--port {new_port}', cmd)
            cmd = re.sub(rf'-p\s*{old_port}\b', f'-p {new_port}', cmd)
            cmd = re.sub(rf'PORT={old_port}\b', f'PORT={new_port}', cmd)
            cmd = re.sub(rf'runserver\s*{old_port}\b', f'runserver {new_port}', cmd)
            return cmd

        # No old port, use adjust method
        return self._adjust_command_port(cmd, new_port)

    def stop_all(self):
        """Stop all running processes."""
        for process_id in list(self._subprocesses.keys()):
            try:
                self.stop(process_id, force=True)
            except Exception:
                pass


# Global process manager
_process_manager: Optional[ProcessManager] = None


def get_process_manager() -> ProcessManager:
    """Get the global process manager."""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager()
    return _process_manager
