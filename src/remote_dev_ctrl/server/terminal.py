"""Terminal session management with PTY support.

Uses a standalone PTY relay process (pty_relay.py) for terminal persistence
across server restarts.  The relay owns the PTY and child process, exposing
Unix sockets for data and control.  Falls back to a raw PTY when the relay
cannot be spawned.
"""

import asyncio
import base64
import json
import logging
import os
import pty
import re
import signal
import socket
import struct
import fcntl
import termios
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Callable
import subprocess

logger = logging.getLogger(__name__)

# Matches: --resume=<uuid> or --resume <uuid>
_RESUME_PATTERN = re.compile(
    r'--resume[= ]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE,
)
# Strip ANSI escape sequences (SGR, OSC, CSI) so they don't break regex matching
_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\[.*?[@-~]')


class TerminalStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class TerminalSession:
    """A terminal session."""
    id: str
    project: str
    command: str
    cwd: str
    pid: Optional[int] = None
    fd: Optional[int] = None  # file descriptor (PTY master or connected socket)
    status: TerminalStatus = TerminalStatus.STARTING
    created_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    cols: int = 120
    rows: int = 30
    relay_name: Optional[str] = None  # relay process name (e.g. "rdc-myproject")
    _ctrl_sock: Optional[socket.socket] = field(default=None, repr=False)
    _output_buffer: bytearray = field(default_factory=bytearray, repr=False)
    _buffer_max: int = 256 * 1024  # 256KB — enough for ~5K lines
    _last_output_at: float = 0.0


# ---------------------------------------------------------------------------
# Relay helpers
# ---------------------------------------------------------------------------

def _relay_socket_dir() -> str:
    """Return (and ensure) the relay socket directory.

    On first call, migrates any relay sockets from legacy ~/.adt/relay/
    to ~/.rdc/relay/ so existing relay processes can be found.
    """
    from .config import get_rdc_home
    d = str(get_rdc_home() / "relay")
    os.makedirs(d, exist_ok=True)

    # One-time migration from ~/.adt/relay to ~/.rdc/relay
    legacy = os.path.join(os.path.expanduser("~"), ".adt", "relay")
    if os.path.isdir(legacy) and legacy != d:
        try:
            for entry in os.listdir(legacy):
                src = os.path.join(legacy, entry)
                dst = os.path.join(d, entry)
                if not os.path.exists(dst):
                    os.symlink(src, dst)
        except Exception:
            pass
    return d


def _relay_script_path() -> str:
    """Return the path to pty_relay.py (next to this file)."""
    return os.path.join(os.path.dirname(__file__), "pty_relay.py")


def _relay_spawn(name: str, cmd: str, cwd: str, cols: int, rows: int) -> bool:
    """Launch a relay as a detached subprocess. Returns True if sockets appear."""
    import sys

    sock_dir = _relay_socket_dir()
    relay_script = _relay_script_path()

    # Launch relay detached from our process group.
    # Augment PATH so helper scripts (rdc-launch) are available.
    env = os.environ.copy()
    rdc_bin = str(Path.home() / ".rdc" / "bin")
    if rdc_bin not in env.get("PATH", ""):
        env["PATH"] = rdc_bin + ":" + env.get("PATH", "")

    try:
        subprocess.Popen(
            [
                sys.executable, relay_script,
                "--name", name,
                "--cmd", cmd,
                "--cwd", cwd,
                "--cols", str(cols),
                "--rows", str(rows),
                "--socket-dir", sock_dir,
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception as e:
        logger.error(f"Failed to launch relay {name}: {e}")
        return False

    # Wait for data socket to appear (up to 3 seconds)
    data_path = os.path.join(sock_dir, f"{name}.data.sock")
    for _ in range(30):
        if os.path.exists(data_path):
            return True
        time.sleep(0.1)

    logger.error(f"Relay {name} did not create socket within timeout")
    return False


def _relay_connect_data(name: str) -> int:
    """Connect to relay data socket. Returns the socket's fileno (fd)."""
    sock_dir = _relay_socket_dir()
    path = os.path.join(sock_dir, f"{name}.data.sock")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)

    # Set non-blocking for use with add_reader / os.read
    sock.setblocking(False)

    # Return the fd — the socket object must stay alive so we don't close it.
    # We'll stash it on the TerminalSession via _data_sock_ref.
    fd = sock.fileno()
    # Prevent GC from closing the socket by attaching it to a module-level dict
    _open_data_sockets[fd] = sock
    return fd


# Keep references to connected data sockets so GC doesn't close them
_open_data_sockets: dict[int, socket.socket] = {}


def _relay_close_data(fd: int):
    """Close a relay data socket by fd."""
    sock = _open_data_sockets.pop(fd, None)
    if sock:
        try:
            sock.close()
        except OSError:
            pass


def _relay_connect_ctrl(name: str) -> socket.socket:
    """Connect to relay control socket."""
    sock_dir = _relay_socket_dir()
    path = os.path.join(sock_dir, f"{name}.ctrl.sock")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path)
    sock.settimeout(5.0)
    return sock


def _relay_send_ctrl(sock: socket.socket, msg: dict) -> dict:
    """Send a JSON-line message on the control socket and read the response."""
    sock.sendall(json.dumps(msg).encode() + b"\n")
    # Read response (may arrive in chunks)
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return {"error": "connection closed"}
        buf += chunk
    line = buf.split(b"\n", 1)[0]
    return json.loads(line)


def _relay_resize(ctrl_sock: socket.socket, cols: int, rows: int) -> bool:
    resp = _relay_send_ctrl(ctrl_sock, {"type": "resize", "cols": cols, "rows": rows})
    return resp.get("ok", False)


def _relay_get_buffer(ctrl_sock: socket.socket) -> bytes:
    resp = _relay_send_ctrl(ctrl_sock, {"type": "buffer"})
    data_b64 = resp.get("data", "")
    if data_b64:
        return base64.b64decode(data_b64)
    return b""


def _relay_status(ctrl_sock: socket.socket) -> dict:
    return _relay_send_ctrl(ctrl_sock, {"type": "status"})


def _relay_kill(ctrl_sock: socket.socket):
    try:
        _relay_send_ctrl(ctrl_sock, {"type": "kill"})
    except (OSError, ConnectionError):
        pass


def _relay_list_sessions() -> list[str]:
    """Scan socket dir for relay sessions. Returns list of relay names."""
    sock_dir = _relay_socket_dir()
    names = []
    try:
        for entry in os.listdir(sock_dir):
            if entry.endswith(".data.sock"):
                names.append(entry.removesuffix(".data.sock"))
    except FileNotFoundError:
        pass
    return names


def _relay_cleanup_stale(name: str):
    """Remove stale socket files for a relay that's no longer running."""
    sock_dir = _relay_socket_dir()
    for suffix in (".data.sock", ".ctrl.sock", ".pid"):
        try:
            os.unlink(os.path.join(sock_dir, f"{name}{suffix}"))
        except FileNotFoundError:
            pass


def _session_meta_path() -> Path:
    """Path to the terminal session metadata file.

    Migrates from legacy ~/.adt/ path on first call if needed.
    """
    from .config import get_rdc_home
    rdc_path = get_rdc_home() / "terminal_sessions.json"
    if not rdc_path.exists():
        legacy = Path.home() / ".adt" / "terminal_sessions.json"
        if legacy.exists():
            import shutil
            shutil.copy2(str(legacy), str(rdc_path))
    return rdc_path


def _save_session_meta(sessions: dict[str, TerminalSession]) -> None:
    """Persist session metadata so it survives server restarts."""
    path = _session_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    for sid, s in sessions.items():
        data[sid] = {
            "project": s.project,
            "command": s.command,
            "cwd": s.cwd,
            "relay_name": s.relay_name,
        }
    try:
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.debug(f"Failed to save session metadata: {e}")


def _load_session_meta() -> dict[str, dict]:
    """Load persisted session metadata."""
    path = _session_meta_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


class TerminalManager:
    """Manages terminal sessions with PTY."""

    def __init__(self):
        self._sessions: dict[str, TerminalSession] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._callbacks: dict[str, list[Callable]] = {}
        self._bg_buffers: dict[str, asyncio.Task] = {}
        self._on_session_stopped: Optional[Callable[[str], None]] = None
        # Snapshots: keyed by (session_id, cols, rows) -> serialized screen data
        self._snapshots: dict[tuple[str, int, int], str] = {}
        # Connected client dimensions: session_id -> {client_id: (cols, rows)}
        self._client_dims: dict[str, dict[str, tuple[int, int]]] = {}
        # Sessions that need SIGWINCH on first client connect (after server restart)
        self._needs_sigwinch: set[str] = set()

    def _notify_session_stopped(self, session_id: str):
        if self._on_session_stopped:
            try:
                self._on_session_stopped(session_id)
            except Exception as e:
                logger.debug(f"On session stopped callback error: {e}")

    def on_output(self, session_id: str, callback: Callable[[bytes], None]):
        """Register callback for terminal output."""
        if session_id not in self._callbacks:
            self._callbacks[session_id] = []
        self._callbacks[session_id].append(callback)

    def remove_callback(self, session_id: str, callback: Callable):
        """Remove a callback."""
        if session_id in self._callbacks:
            self._callbacks[session_id] = [c for c in self._callbacks[session_id] if c != callback]

    def _emit_output(self, session_id: str, data: bytes):
        """Emit output to all callbacks."""
        callbacks = self._callbacks.get(session_id, [])
        logger.debug(f"Emitting {len(data)} bytes to {len(callbacks)} callbacks for {session_id}")
        for callback in callbacks:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Callback error: {e}")
                pass

    def _capture_agent_session(self, session_id: str):
        """Scan output buffer for cursor-agent session ID and store it."""
        session = self._sessions.get(session_id)
        if not session:
            return
        try:
            raw_tail = bytes(session._output_buffer[-2048:]).decode("utf-8", errors="replace")
            # Strip ANSI escape codes so they don't break UUID matching
            tail = _ANSI_ESCAPE.sub('', raw_tail)
            match = _RESUME_PATTERN.search(tail)
            if not match:
                logger.debug(f"No agent session ID found in tail of {session_id} ({len(raw_tail)} chars)")
                return
            agent_sid = match.group(1)
            # Resolve project name -> project_id
            from .db.repositories import get_agent_session_repo, resolve_project_id
            project_id = resolve_project_id(session.project)
            if not project_id:
                logger.warning(f"Cannot store agent session: project '{session.project}' not found in DB")
                return
            repo = get_agent_session_repo()
            repo.create(project_id, agent_sid)
            logger.info(f"Captured agent session {agent_sid} for project {session.project}")
        except Exception as e:
            logger.warning(f"Failed to capture agent session for {session_id}: {e}")


    def create(
        self,
        project: str,
        command: Optional[str] = None,
        cwd: Optional[str] = None,
        cols: int = 120,
        rows: int = 30,
        mode: Optional[str] = None,
    ) -> TerminalSession:
        """Create a new terminal session.

        Args:
            project: Project name (used as session ID)
            command: Command to run (default: cursor-agent or bash)
            cwd: Working directory (default: project path)
            cols: Terminal columns
            rows: Terminal rows
            mode: Launch mode — "new" (default), "continue", or "resume:<session-id>"
        """
        from .db.repositories import get_project_repo

        # Generate unique session ID (supports multiple terminals per project)
        # Match exact "term-{project}" or "term-{project}-N" (not "term-{project}foo")
        _prefix = f"term-{project}"
        existing_ids = [
            sid for sid in self._sessions
            if sid == _prefix or sid.startswith(f"{_prefix}-")
        ]
        if not existing_ids:
            session_id = _prefix
        else:
            idx = 1
            while f"{_prefix}-{idx}" in self._sessions:
                idx += 1
            session_id = f"{_prefix}-{idx}"

        # Get project path from DB
        proj = get_project_repo().get(project)

        if not cwd:
            if proj:
                cwd = proj.path
            else:
                cwd = os.getcwd()

        # Get command from: 1) explicit param, 2) project DB config, 3) env var, 4) login shell
        # Explicit empty string means "plain login shell"
        if command == "":
            command = os.environ.get("SHELL", "/bin/bash")
        elif not command:
            # Try to get from project DB config
            from .db.repositories import ProjectRepository
            project_repo = ProjectRepository()
            db_proj = project_repo.get(project)

            if db_proj and db_proj.config and db_proj.config.get("terminal_command"):
                command = db_proj.config["terminal_command"]
            else:
                command = os.environ.get("RDC_TERMINAL_CMD", os.environ.get("ADT_TERMINAL_CMD", os.environ.get("SHELL", "/bin/bash")))

        # Apply launch mode: resume (specific session), or resume/continue.
        # Skip if command uses rdc-launch (it handles resume logic itself).
        if "rdc-launch" not in (command or ""):
            _AGENT_CONTINUE_CMDS = ("cursor-agent", "claude")
            if mode and mode.startswith("resume:"):
                sid = mode.split(":", 1)[1]
                command = f"{command} --resume={sid}"
            elif mode in ("continue", None, "new") and "--resume" not in command and "--continue" not in command:
                raw_base = ((command or "").strip().split() or [""])[0]
                base_cmd = os.path.basename(raw_base) if raw_base else ""
                if base_cmd not in _AGENT_CONTINUE_CMDS:
                    base_cmd = ""
                try:
                    from .db.repositories import get_agent_session_repo, resolve_project_id
                    project_id = resolve_project_id(project)
                    if project_id and base_cmd:
                        repo = get_agent_session_repo()
                        latest = repo.get_latest(project_id)
                        if latest and base_cmd == "cursor-agent":
                            command = f"{command} --resume={latest.agent_session_id}"
                        elif mode == "continue":
                            command = f"{command} --continue"
                except Exception as e:
                    logger.debug(f"Could not attach resume/continue for {project}: {e}")
                    if mode == "continue" and base_cmd:
                        command = f"{command} --continue"

        relay_name = f"rdc-{session_id.removeprefix('term-')}"

        # Check if a surviving relay exists and reconnect
        if relay_name in _relay_list_sessions():
            try:
                return self._reconnect_relay(session_id, project, relay_name, command, cwd, cols, rows)
            except Exception as e:
                logger.warning(f"Failed to reconnect to relay {relay_name}: {e}")
                _relay_cleanup_stale(relay_name)

        # Try relay, fall back to raw PTY
        return self._create_relay(session_id, project, relay_name, command, cwd, cols, rows)

    def _create_relay(
        self, session_id: str, project: str, relay_name: str,
        command: str, cwd: str, cols: int, rows: int,
    ) -> TerminalSession:
        """Create a terminal backed by a relay process."""
        session = TerminalSession(
            id=session_id, project=project, command=command,
            cwd=cwd, cols=cols, rows=rows, relay_name=relay_name,
        )
        try:
            if not _relay_spawn(relay_name, command, cwd, cols, rows):
                logger.warning(f"Relay spawn failed for {relay_name}, falling back to raw PTY")
                return self._create_raw_pty(session_id, project, command, cwd, cols, rows)

            data_fd = _relay_connect_data(relay_name)
            ctrl_sock = _relay_connect_ctrl(relay_name)

            session.fd = data_fd
            session._ctrl_sock = ctrl_sock
            session.status = TerminalStatus.RUNNING

            # Get the child PID from relay
            status_resp = _relay_status(ctrl_sock)
            session.pid = status_resp.get("pid")

            self._sessions[session_id] = session
            _save_session_meta(self._sessions)
            logger.info(
                f"Terminal created (relay): {session_id}, relay={relay_name}, "
                f"child_pid={session.pid}, fd={data_fd}, cmd={command}"
            )
            self._start_background_buffer(session_id)
        except Exception as e:
            logger.error(f"Failed to create relay terminal {session_id}: {e}")
            # Try to clean up
            try:
                ctrl = _relay_connect_ctrl(relay_name)
                _relay_kill(ctrl)
                ctrl.close()
            except Exception:
                pass
            # Fall back to raw PTY
            logger.info(f"Falling back to raw PTY for {session_id}")
            return self._create_raw_pty(session_id, project, command, cwd, cols, rows)
        return session

    def _reconnect_relay(
        self, session_id: str, project: str, relay_name: str,
        command: str, cwd: str, cols: int, rows: int,
    ) -> TerminalSession:
        """Reconnect to a surviving relay process after server restart."""
        ctrl_sock = _relay_connect_ctrl(relay_name)
        status_resp = _relay_status(ctrl_sock)

        if not status_resp.get("alive", False):
            ctrl_sock.close()
            _relay_cleanup_stale(relay_name)
            raise ConnectionError(f"Relay {relay_name} child is dead")

        # Use relay's current size
        actual_cols = status_resp.get("cols", cols)
        actual_rows = status_resp.get("rows", rows)

        session = TerminalSession(
            id=session_id, project=project, command=command,
            cwd=cwd, cols=actual_cols, rows=actual_rows, relay_name=relay_name,
        )

        data_fd = _relay_connect_data(relay_name)

        session.fd = data_fd
        session._ctrl_sock = ctrl_sock
        session.pid = status_resp.get("pid")
        session.status = TerminalStatus.RUNNING

        self._sessions[session_id] = session
        _save_session_meta(self._sessions)
        logger.info(
            f"Terminal reconnected (relay): {session_id}, relay={relay_name}, "
            f"child_pid={session.pid}"
        )

        # The relay sends its ring buffer on data socket connect.
        # Start the background reader to ingest it — but mark the session
        # as needing a SIGWINCH when the first client connects. The client's
        # resize will trigger a clean repaint at the correct dimensions.
        self._start_background_buffer(session_id)
        self._needs_sigwinch.add(session_id)

        return session

    def _create_raw_pty(
        self, session_id: str, project: str,
        command: str, cwd: str, cols: int, rows: int,
    ) -> TerminalSession:
        """Create a terminal with a raw PTY (no relay, fallback behaviour)."""
        session = TerminalSession(
            id=session_id, project=project, command=command,
            cwd=cwd, cols=cols, rows=rows,
        )
        try:
            master_fd, slave_fd = pty.openpty()

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            # Ensure ~/.rdc/bin is in PATH for helper scripts (rdc-launch, etc.)
            rdc_bin = str(Path.home() / ".rdc" / "bin")
            if rdc_bin not in env.get("PATH", ""):
                env["PATH"] = rdc_bin + ":" + env.get("PATH", "")

            process = subprocess.Popen(
                command,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                preexec_fn=os.setsid,
            )

            os.close(slave_fd)

            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            session.pid = process.pid
            session.fd = master_fd
            session.status = TerminalStatus.RUNNING

            self._sessions[session_id] = session
            _save_session_meta(self._sessions)
            logger.info(f"Terminal created (raw): {session_id}, pid={process.pid}, fd={master_fd}, cmd={command}")
            self._start_background_buffer(session_id)
        except Exception as e:
            logger.error(f"Failed to create terminal {session_id}: {e}")
            session.status = TerminalStatus.ERROR
            session.error = str(e)
            self._sessions[session_id] = session
        return session

    def _start_background_buffer(self, session_id: str):
        """Start background task to poll PTY output before any WS client connects.

        Buffers output so reconnect replays work.  Cancelled when start_reader()
        switches to the event-loop add_reader approach.
        """
        session = self._sessions.get(session_id)
        if not session or session.fd is None:
            return

        fd = session.fd

        async def _buffer_loop():
            while session_id in self._sessions and session_id not in self._readers:
                try:
                    data = os.read(fd, 65536)
                    if data:
                        session._output_buffer.extend(data)
                        if len(session._output_buffer) > session._buffer_max:
                            session._output_buffer = session._output_buffer[-session._buffer_max:]
                        session._last_output_at = time.monotonic()
                        self._emit_output(session_id, data)
                    else:
                        # EOF — child exited; log what it said
                        buf_preview = bytes(session._output_buffer[-500:]).decode(
                            "utf-8", errors="replace"
                        )
                        logger.warning(
                            f"Background buffer: EOF on {session_id}. "
                            f"Last output: {buf_preview!r}"
                        )
                        self._capture_agent_session(session_id)
                        session.status = TerminalStatus.STOPPED
                        self._notify_session_stopped(session_id)
                        break
                except BlockingIOError:
                    pass
                except OSError as e:
                    import errno as _errno
                    if e.errno == _errno.EIO:
                        buf_preview = bytes(session._output_buffer[-500:]).decode(
                            "utf-8", errors="replace"
                        )
                        logger.warning(
                            f"Background buffer: {session_id} EIO. "
                            f"Last output: {buf_preview!r}"
                        )
                        self._capture_agent_session(session_id)
                        session.status = TerminalStatus.STOPPED
                        self._notify_session_stopped(session_id)
                    break

                await asyncio.sleep(0.1)  # Poll 10 times/sec

            self._bg_buffers.pop(session_id, None)

        try:
            loop = asyncio.get_event_loop()
            self._bg_buffers[session_id] = loop.create_task(_buffer_loop())
        except Exception as e:
            logger.debug(f"Could not start background buffer for {session_id}: {e}")

    def start_reader(self, session_id: str):
        """Start async reader for terminal output using loop.add_reader."""
        # Cancel background buffer — the add_reader approach takes over
        bg = self._bg_buffers.pop(session_id, None)
        if bg:
            bg.cancel()
            logger.debug(f"Cancelled background buffer for {session_id}")

        if session_id in self._readers:
            return  # Already running

        session = self._sessions.get(session_id)
        if not session or session.fd is None:
            logger.warning(f"Cannot start reader for {session_id}: no session or fd")
            return

        fd = session.fd

        def on_readable():
            """Called by event loop when fd has data."""
            if session_id not in self._sessions:
                return
            try:
                data = os.read(fd, 4096)
                if data:
                    logger.debug(f"Read {len(data)} bytes from {session_id}")
                    # Buffer output for replay on reconnect
                    session._output_buffer.extend(data)
                    if len(session._output_buffer) > session._buffer_max:
                        session._output_buffer = session._output_buffer[-session._buffer_max:]
                    session._last_output_at = time.monotonic()
                    self._emit_output(session_id, data)
                else:
                    buf_preview = bytes(session._output_buffer[-500:]).decode(
                        "utf-8", errors="replace"
                    )
                    # Try to capture the exit code
                    exit_info = ""
                    if session.pid and not session.relay_name:
                        try:
                            _, status = os.waitpid(session.pid, os.WNOHANG)
                            if os.WIFEXITED(status):
                                exit_info = f" exit_code={os.WEXITSTATUS(status)}"
                            elif os.WIFSIGNALED(status):
                                exit_info = f" killed_by_signal={os.WTERMSIG(status)}"
                        except ChildProcessError:
                            exit_info = " (not a direct child)"
                        except Exception as ex:
                            exit_info = f" (waitpid err: {ex})"
                    logger.warning(
                        f"EOF on {session_id}, process exited.{exit_info} "
                        f"Last output: {buf_preview!r}"
                    )
                    self._capture_agent_session(session_id)
                    self._stop_reader(session_id)
                    self._sessions[session_id].status = TerminalStatus.STOPPED
                    self._notify_session_stopped(session_id)
            except BlockingIOError:
                pass
            except OSError as e:
                import errno as _errno
                if e.errno == _errno.EIO:
                    logger.warning(f"Terminal {session_id} process exited (EIO on read)")
                else:
                    logger.error(f"Read error for {session_id}: {e}")
                self._capture_agent_session(session_id)
                self._stop_reader(session_id)
                self._sessions[session_id].status = TerminalStatus.STOPPED
                self._notify_session_stopped(session_id)

        try:
            loop = asyncio.get_event_loop()
            loop.add_reader(fd, on_readable)
            self._readers[session_id] = fd
            logger.info(f"Reader added for {session_id}, fd={fd}")
        except Exception as e:
            logger.error(f"Failed to add reader for {session_id}: {e}")

    def _stop_reader(self, session_id: str):
        """Stop the reader for a session."""
        if session_id in self._readers:
            fd = self._readers[session_id]
            try:
                loop = asyncio.get_event_loop()
                loop.remove_reader(fd)
            except Exception:
                pass
            del self._readers[session_id]

    def _read_fd(self, fd: int) -> Optional[bytes]:
        """Read from file descriptor with select."""
        import select
        try:
            # Wait up to 50ms for data
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    data = os.read(fd, 4096)
                    if data:
                        return data
                except BlockingIOError:
                    pass
            return None
        except (OSError, ValueError) as e:
            logger.warning(f"Read fd {fd} error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected read error on fd {fd}: {e}")
            return None

    def write(self, session_id: str, data: bytes) -> bool:
        """Write to terminal stdin."""
        session = self._sessions.get(session_id)
        if not session or session.fd is None:
            logger.warning(f"Write failed: session {session_id} not found or no fd")
            return False

        try:
            written = os.write(session.fd, data)
            logger.debug(f"Wrote {written} bytes to {session_id}")
            return True
        except OSError as e:
            import errno
            if e.errno == errno.EIO:
                logger.warning(f"Terminal {session_id} process dead (EIO on write), marking stopped")
                self._stop_reader(session_id)
                session.status = TerminalStatus.STOPPED
                self._notify_session_stopped(session_id)
            else:
                logger.error(f"Write error to {session_id}: {e}")
            return False

    def register_client(self, session_id: str, client_id: str, cols: int, rows: int) -> None:
        """Register a connected client's viewport dimensions.

        Each client renders independently via xterm.js reflow. The PTY
        is only resized when there's exactly one client (single-user mode)
        or when a client explicitly sends input (active-user mode).
        When multiple clients are connected, the PTY stays at the first
        client's dimensions — other clients get xterm.js-level reflow.
        """
        if session_id not in self._client_dims:
            self._client_dims[session_id] = {}

        is_first = len(self._client_dims[session_id]) == 0
        self._client_dims[session_id][client_id] = (cols, rows)

        if is_first or len(self._client_dims[session_id]) == 1:
            # Single client — resize PTY to match
            self._resize_pty(session_id, cols, rows)
        # Multiple clients: don't resize, each client's xterm.js handles reflow

    def unregister_client(self, session_id: str, client_id: str) -> None:
        """Remove a disconnected client. If one remains, resize PTY to match it."""
        clients = self._client_dims.get(session_id, {})
        clients.pop(client_id, None)
        if len(clients) == 1:
            # Single client left — resize PTY to match it
            remaining = list(clients.values())[0]
            self._resize_pty(session_id, remaining[0], remaining[1])

    def resize_for_active_client(self, session_id: str, client_id: str) -> None:
        """Resize PTY to match a specific client (called when client sends input)."""
        clients = self._client_dims.get(session_id, {})
        dims = clients.get(client_id)
        if dims:
            self._resize_pty(session_id, dims[0], dims[1])

    def redraw_for_client(self, session_id: str, client_id: str) -> bool:
        """Force a repaint for a specific client without changing its dimensions."""
        clients = self._client_dims.get(session_id, {})
        dims = clients.get(client_id)
        if not dims:
            return False
        return self._resize_pty(session_id, dims[0], dims[1], force=True)

    def resize(self, session_id: str, cols: int, rows: int, client_id: str | None = None) -> bool:
        """Resize terminal.

        If client_id is provided, registers the client's dimensions and
        resizes to the max across all clients. Without client_id, resizes
        directly (backward compat).
        """
        if client_id:
            self.register_client(session_id, client_id, cols, rows)
            return True
        return self._resize_pty(session_id, cols, rows)

    def _resize_pty(self, session_id: str, cols: int, rows: int, force: bool = False) -> bool:
        """Actually resize the PTY file descriptor."""
        session = self._sessions.get(session_id)
        if not session or session.fd is None:
            return False

        # Force SIGWINCH on first client connect after server restart,
        # even if dimensions match. This makes the program repaint cleanly
        # instead of relying on the garbled ring buffer replay.
        pending_sigwinch = session_id in self._needs_sigwinch
        if pending_sigwinch:
            self._needs_sigwinch.discard(session_id)
            # Clear stale ring buffer data so client gets only fresh repaint
            session._output_buffer.clear()

        if not force and not pending_sigwinch and session.cols == cols and session.rows == rows:
            return True

        try:
            if session.relay_name and session._ctrl_sock:
                _relay_resize(session._ctrl_sock, cols, rows)
            else:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(session.fd, termios.TIOCSWINSZ, winsize)
            session.cols = cols
            session.rows = rows
            return True
        except Exception:
            return False

    def is_alive(self, session_id: str) -> bool:
        """Check if the terminal's child process is still running."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        # For relay-backed sessions, check via control socket
        if session.relay_name and session._ctrl_sock:
            try:
                resp = _relay_status(session._ctrl_sock)
                return resp.get("alive", False)
            except (socket.timeout, TimeoutError):
                return True
            except (OSError, ConnectionError):
                return False

        # Raw PTY: check the direct child PID
        if not session.pid:
            return False
        try:
            os.kill(session.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def get_buffer(self, session_id: str) -> bytes:
        """Get buffered output for replay on reconnect.

        The rolling buffer may have been truncated mid-escape-sequence or
        mid-UTF-8 codepoint. We sanitize the start of the buffer and
        prepend a terminal reset so xterm.js starts from a clean state.
        """
        session = self._sessions.get(session_id)
        if not session:
            return b""
        buf = bytes(session._output_buffer)
        if not buf:
            return buf

        # Find a clean start point in the buffer.
        start = self._find_clean_start(buf)

        # Prepend reset sequences before the replay:
        # - ESC c = RIS (Reset to Initial State) — clears screen, resets parser
        # - ESC [ ? 25 h = show cursor (some programs hide it)
        # - ESC [ 0 m = reset attributes (colors, bold, etc.)
        reset = b"\x1bc\x1b[?25h\x1b[0m"

        return reset + buf[start:]

    @staticmethod
    def _find_clean_start(buf: bytes) -> int:
        """Find the first byte in buf that starts a clean sequence.

        Skips:
        - Leading UTF-8 continuation bytes (truncated multi-byte char)
        - Bytes inside a truncated CSI sequence (ESC [ ... <params>)
        - Bytes inside a truncated OSC sequence (ESC ] ... ST)
        """
        start = 0
        length = len(buf)

        # Skip leading UTF-8 continuation bytes (0x80-0xBF)
        while start < length and 0x80 <= buf[start] <= 0xBF:
            start += 1

        if start >= length:
            return start

        # If we start on a clean byte, we're good
        b = buf[start]
        if b == 0x1B or b == 0x0A or b == 0x0D or (0x20 <= b <= 0x7E):
            return start

        # We're mid-sequence. Scan forward for a safe resume point.
        # Look for: newline, or ESC at the start of a new sequence.
        scan_limit = min(start + 4096, length)
        i = start
        while i < scan_limit:
            b = buf[i]
            if b == 0x0A:
                # Newline — safe to start on next line
                return i + 1
            if b == 0x1B and i + 1 < scan_limit:
                nb = buf[i + 1]
                # ESC [ = CSI, ESC ] = OSC, ESC ( = charset — all valid starts
                if nb in (0x5B, 0x5D, 0x28, 0x29, 0x63):
                    return i
            i += 1

        # Couldn't find a clean point in the scan window — skip to start
        return start

    def store_snapshot(self, session_id: str, cols: int, rows: int, data: str) -> None:
        """Store a serialized screen snapshot from a client.

        Keyed by (session_id, cols, rows) so clients at different dimensions
        each have their own snapshot. Limited to 5 snapshots per session.
        """
        if not data or cols <= 0 or rows <= 0 or len(data) > 512_000:
            return
        key = (session_id, cols, rows)
        self._snapshots[key] = data
        # Evict old snapshots for this session (keep last 5 dimension combos)
        session_keys = [k for k in self._snapshots if k[0] == session_id]
        if len(session_keys) > 5:
            for old_key in session_keys[:-5]:
                self._snapshots.pop(old_key, None)

    def get_snapshot(self, session_id: str, cols: int, rows: int) -> str | None:
        """Get a stored snapshot matching the exact dimensions."""
        return self._snapshots.get((session_id, cols, rows))

    def get_best_snapshot(self, session_id: str, cols: int, rows: int) -> str | None:
        """Get the best available snapshot for a session.

        Prefer exact dimensions. Otherwise, use the closest stored dimensions
        for the same session rather than falling back straight to raw byte replay.
        """
        exact = self.get_snapshot(session_id, cols, rows)
        if exact:
            return exact

        candidates = [
            (snap_cols, snap_rows, data)
            for (snap_session_id, snap_cols, snap_rows), data in self._snapshots.items()
            if snap_session_id == session_id
        ]
        if not candidates:
            return None

        candidates.sort(key=lambda item: (abs(item[0] - cols) + abs(item[1] - rows), -item[0], -item[1]))
        return candidates[0][2]

    def is_waiting_for_input(self, session_id: str) -> bool:
        """Heuristic: is this terminal waiting for user input?"""
        session = self._sessions.get(session_id)
        if not session or session.status != TerminalStatus.RUNNING:
            return False

        idle_seconds = time.monotonic() - session._last_output_at
        if idle_seconds < 3 or session._last_output_at == 0.0:
            return False

        # Check last line of buffer for prompt patterns
        last_lines = bytes(session._output_buffer[-512:]).decode("utf-8", errors="replace")
        last_line = last_lines.rstrip().split("\n")[-1].strip()

        prompt_patterns = ["$ ", "> ", "? ", "(y/n)", "(Y/n)", "approve", "confirm", "Continue?"]
        return any(p in last_line for p in prompt_patterns)

    def check_health(self):
        """Update internal state only. Do not mark sessions stopped here — we only set
        STOPPED when we get actual EOF/EIO from the PTY (process exit signal). That way
        we never assume the terminal is dead without the exit signal.
        """

    def restart(self, session_id: str, mode: Optional[str] = None) -> Optional[TerminalSession]:
        """Destroy a dead/running terminal and create a fresh one for the same project."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        project = session.project
        command = session.command
        self.destroy(session_id)
        return self.create(project=project, command=command, mode=mode)

    def get(self, session_id: str) -> Optional[TerminalSession]:
        """Get a session."""
        return self._sessions.get(session_id)

    def get_by_project(self, project: str) -> list[TerminalSession]:
        """Get all sessions for a project."""
        return [
            s for s in self._sessions.values()
            if s.project == project
        ]

    def list(self) -> list[TerminalSession]:
        """List all sessions (updates health first)."""
        self.check_health()
        return list(self._sessions.values())

    def _detach(self, session_id: str) -> bool:
        """Detach from a terminal session without killing the underlying relay.

        Closes the data socket fd, cancels background tasks and readers,
        and removes the session from memory — but leaves the relay process alive
        so it can be rediscovered on next startup.
        """
        session = self._sessions.get(session_id)
        if not session:
            return False

        logger.info(f"Detaching {session_id} (relay={session.relay_name})")

        # Cancel background buffer
        bg = self._bg_buffers.pop(session_id, None)
        if bg:
            bg.cancel()

        # Stop reader
        self._stop_reader(session_id)

        # Close data socket fd
        if session.fd is not None:
            _relay_close_data(session.fd)

        # Close control socket
        if session._ctrl_sock is not None:
            try:
                session._ctrl_sock.close()
            except OSError:
                pass

        # Clean up callbacks
        self._callbacks.pop(session_id, None)
        del self._sessions[session_id]
        return True

    def destroy(self, session_id: str) -> bool:
        """Destroy a terminal session (kills relay if present)."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        # Cancel background buffer
        bg = self._bg_buffers.pop(session_id, None)
        if bg:
            bg.cancel()

        # Stop reader
        self._stop_reader(session_id)

        # Kill relay (which kills child)
        if session.relay_name and session._ctrl_sock:
            _relay_kill(session._ctrl_sock)
            try:
                session._ctrl_sock.close()
            except OSError:
                pass
        elif session.pid:
            # Raw PTY: kill the process group
            try:
                os.killpg(os.getpgid(session.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        # Close data FD
        if session.fd is not None:
            if session.relay_name:
                _relay_close_data(session.fd)
            else:
                try:
                    os.close(session.fd)
                except Exception:
                    pass

        # Clean up callbacks, snapshots, and client dimensions
        self._callbacks.pop(session_id, None)
        self._client_dims.pop(session_id, None)
        self._snapshots = {k: v for k, v in self._snapshots.items() if k[0] != session_id}
        del self._sessions[session_id]
        _save_session_meta(self._sessions)
        return True

    def destroy_all(self):
        """Detach all terminal sessions (used during shutdown).

        For relay-backed sessions this detaches without killing, so they
        survive server restarts.  For raw PTY sessions this fully destroys.
        """
        for session_id in list(self._sessions):
            session = self._sessions.get(session_id)
            if session and session.relay_name:
                self._detach(session_id)
            else:
                self.destroy(session_id)

    def destroy_by_project(self, project: str) -> bool:
        """Destroy all sessions for a project."""
        sessions = self.get_by_project(project)
        for s in sessions:
            self.destroy(s.id)
        return len(sessions) > 0

    async def rediscover_sessions(self):
        """Re-attach to surviving relay processes after server restart.

        Called once during startup.  Scans the socket directory for relay
        sessions and reconnects to each one.

        Relay names follow the pattern ``rdc-{project}`` or ``rdc-{project}-N``
        for multi-terminal sessions.  We resolve the project by matching the
        relay suffix against known project names (longest match first) to
        avoid mis-parsing project names that contain hyphens.
        """
        relay_names = _relay_list_sessions()
        # Filter to rdc-* relays (also accept legacy adt-* relays)
        relay_names = [n for n in relay_names if n.startswith("rdc-") or n.startswith("adt-")]

        if not relay_names:
            logger.info("No surviving relay sessions found")
            return

        # Load known project names for matching
        try:
            from .db.repositories import get_project_repo
            known_projects = sorted(
                [p.name for p in get_project_repo().list()],
                key=len, reverse=True,  # longest first for greedy match
            )
        except Exception:
            known_projects = []

        # Load persisted session metadata for accurate command/project info
        saved_meta = _load_session_meta()

        logger.info(f"Found {len(relay_names)} surviving relay session(s): {relay_names}")
        for relay_name in relay_names:
            # Derive project and session_id from relay name.
            # relay_name is "rdc-{project}" or "rdc-{project}-N" (or legacy "adt-*").
            # session_id mirrors it as "term-{project}" or "term-{project}-N".
            prefix = "rdc-" if relay_name.startswith("rdc-") else "adt-"
            suffix = relay_name.removeprefix(prefix)  # e.g. "myproj" or "myproj-2"

            # Match against known project names
            project = None
            for pname in known_projects:
                if suffix == pname or suffix.startswith(f"{pname}-"):
                    project = pname
                    break

            if not project:
                # Fallback: assume the whole suffix is the project (legacy single-terminal)
                project = suffix

            session_id = f"term-{suffix}"

            if session_id in self._sessions:
                logger.debug(f"Session {session_id} already tracked, skipping")
                continue

            # Resolve cwd from project config
            try:
                proj = next((p for p in config.projects if p.name == project), None)
                cwd = str(proj.path) if proj else os.getcwd()
            except Exception:
                cwd = os.getcwd()

            # Restore command from persisted metadata, fall back to project config
            meta = saved_meta.get(session_id, {})
            command = meta.get("command", "")
            if meta.get("project"):
                project = meta["project"]
            if meta.get("cwd"):
                cwd = meta["cwd"]
            if not command:
                try:
                    from .db.repositories import ProjectRepository
                    project_repo = ProjectRepository()
                    db_proj = project_repo.get(project)
                    if db_proj and db_proj.config and db_proj.config.get("terminal_command"):
                        command = db_proj.config["terminal_command"]
                    else:
                        command = os.environ.get("RDC_TERMINAL_CMD", os.environ.get("ADT_TERMINAL_CMD", os.environ.get("SHELL", "/bin/bash")))
                except Exception:
                    command = os.environ.get("SHELL", "/bin/bash")

            try:
                self._reconnect_relay(
                    session_id=session_id,
                    project=project,
                    relay_name=relay_name,
                    command=command,
                    cwd=cwd,
                    cols=120,
                    rows=30,
                )
            except Exception as e:
                logger.warning(f"Failed to reconnect relay {relay_name}: {e}")
                _relay_cleanup_stale(relay_name)


# Global instance
_terminal_manager: Optional[TerminalManager] = None


def get_terminal_manager() -> TerminalManager:
    """Get the global terminal manager."""
    global _terminal_manager
    if _terminal_manager is None:
        _terminal_manager = TerminalManager()
    return _terminal_manager
