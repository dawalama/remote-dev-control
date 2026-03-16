#!/usr/bin/env python3
"""Standalone PTY relay process.

Owns a PTY + child process and exposes Unix sockets for the server to
connect/reconnect.  Survives server restarts — the server just reconnects
to the data socket and picks up where it left off.

Usage:
    python pty_relay.py --name <name> --cmd <cmd> --cwd <dir> \
                        --cols N --rows N --socket-dir <dir>

Socket paths:
    <socket-dir>/<name>.data.sock   — bidirectional byte stream
    <socket-dir>/<name>.ctrl.sock   — JSON-line control protocol
    <socket-dir>/<name>.pid         — PID file
"""

import argparse
import base64
import fcntl
import json
import logging
import os
import pty
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import time

logger = logging.getLogger("pty_relay")

# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------

class RingBuffer:
    """Fixed-size circular byte buffer."""

    def __init__(self, capacity: int = 1024 * 1024):
        self._buf = bytearray(capacity)
        self._capacity = capacity
        self._write_pos = 0
        self._length = 0  # how many bytes are actually stored

    def write(self, data: bytes):
        n = len(data)
        if n >= self._capacity:
            # Data larger than buffer — just keep the tail
            data = data[-self._capacity:]
            n = self._capacity
            self._buf[:] = data
            self._write_pos = 0
            self._length = self._capacity
            return
        # Write in up to two chunks (wrap around)
        end = self._write_pos + n
        if end <= self._capacity:
            self._buf[self._write_pos:end] = data
        else:
            first = self._capacity - self._write_pos
            self._buf[self._write_pos:] = data[:first]
            self._buf[:n - first] = data[first:]
        self._write_pos = end % self._capacity
        self._length = min(self._length + n, self._capacity)

    def read_all(self) -> bytes:
        """Return all buffered content in order."""
        if self._length == 0:
            return b""
        if self._length < self._capacity:
            # Haven't wrapped yet
            start = self._write_pos - self._length
            return bytes(self._buf[start:self._write_pos])
        # Wrapped: read from write_pos to end, then start to write_pos
        return bytes(self._buf[self._write_pos:]) + bytes(self._buf[:self._write_pos])


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------

class PtyRelay:
    def __init__(self, name: str, cmd: str, cwd: str, cols: int, rows: int, socket_dir: str):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.socket_dir = socket_dir
        self.start_time = time.monotonic()

        self.ring = RingBuffer()
        self.sel = selectors.DefaultSelector()

        # PTY
        self.master_fd: int = -1
        self.child_pid: int = -1

        # Sockets
        self.data_srv: socket.socket | None = None
        self.ctrl_srv: socket.socket | None = None
        self.data_client: socket.socket | None = None
        self.ctrl_client: socket.socket | None = None

        # Paths
        self.data_sock_path = os.path.join(socket_dir, f"{name}.data.sock")
        self.ctrl_sock_path = os.path.join(socket_dir, f"{name}.ctrl.sock")
        self.pid_path = os.path.join(socket_dir, f"{name}.pid")

        self._running = True
        self._child_alive = True

    # -- Setup ---------------------------------------------------------------

    def start(self):
        """Spawn child, bind sockets, enter event loop."""
        os.makedirs(self.socket_dir, exist_ok=True)
        self._cleanup_stale_sockets()
        self._spawn_child()
        self._bind_sockets()
        self._write_pid_file()
        self._install_signals()
        logger.info(f"Relay {self.name} started: pid={self.child_pid}, "
                    f"data={self.data_sock_path}, ctrl={self.ctrl_sock_path}")
        self._event_loop()

    def _cleanup_stale_sockets(self):
        for p in (self.data_sock_path, self.ctrl_sock_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass

    def _spawn_child(self):
        master_fd, slave_fd = pty.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        proc = subprocess.Popen(
            self.cmd,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=env,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)

        # Non-blocking master
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self.master_fd = master_fd
        self.child_pid = proc.pid

        # Register PTY master for reading
        self.sel.register(master_fd, selectors.EVENT_READ, self._on_pty_read)

    def _bind_sockets(self):
        # Data socket
        self.data_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.data_srv.bind(self.data_sock_path)
        self.data_srv.listen(1)
        self.data_srv.setblocking(False)
        self.sel.register(self.data_srv, selectors.EVENT_READ, self._on_data_accept)

        # Control socket
        self.ctrl_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ctrl_srv.bind(self.ctrl_sock_path)
        self.ctrl_srv.listen(1)
        self.ctrl_srv.setblocking(False)
        self.sel.register(self.ctrl_srv, selectors.EVENT_READ, self._on_ctrl_accept)

    def _write_pid_file(self):
        with open(self.pid_path, "w") as f:
            f.write(str(os.getpid()))

    def _install_signals(self):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGCHLD, self._handle_sigchld)

    def _handle_sigterm(self, signum, frame):
        logger.info(f"Relay {self.name} received SIGTERM, shutting down")
        self._kill_child()
        self._running = False

    def _handle_sigchld(self, signum, frame):
        # Reap the child
        try:
            pid, status = os.waitpid(self.child_pid, os.WNOHANG)
            if pid != 0:
                logger.info(f"Child {self.child_pid} exited with status {status}")
                self._child_alive = False
        except ChildProcessError:
            self._child_alive = False

    # -- Event loop ----------------------------------------------------------

    def _event_loop(self):
        while self._running:
            try:
                events = self.sel.select(timeout=1.0)
            except (OSError, ValueError):
                break

            for key, mask in events:
                callback = key.data
                try:
                    callback(key.fileobj)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

            # Check if child exited and we've drained output
            if not self._child_alive:
                # Try one last drain
                self._drain_pty()
                self._running = False

        self._cleanup()

    def _drain_pty(self):
        """Read any remaining data from PTY after child exit."""
        while True:
            try:
                data = os.read(self.master_fd, 65536)
                if not data:
                    break
                self.ring.write(data)
                self._send_to_data_client(data)
            except (BlockingIOError, OSError):
                break

    # -- PTY read ------------------------------------------------------------

    def _on_pty_read(self, fd):
        try:
            data = os.read(fd, 65536)
            if not data:
                # EOF — child exited
                logger.info(f"PTY EOF for {self.name}")
                self._child_alive = False
                return
            self.ring.write(data)
            self._send_to_data_client(data)
        except BlockingIOError:
            pass
        except OSError as e:
            import errno
            if e.errno == errno.EIO:
                logger.info(f"PTY EIO for {self.name} — child exited")
                self._child_alive = False
            else:
                logger.error(f"PTY read error: {e}")
                self._child_alive = False

    def _send_to_data_client(self, data: bytes):
        if self.data_client is None:
            return
        try:
            self.data_client.sendall(data)
        except BlockingIOError:
            logger.debug(f"Data client send buffer full for {self.name}, dropping %d bytes", len(data))
        except (BrokenPipeError, ConnectionResetError, OSError):
            logger.info(f"Data client disconnected for {self.name}")
            self._disconnect_data_client()

    # -- Data socket ---------------------------------------------------------

    def _on_data_accept(self, srv_sock):
        conn, _ = srv_sock.accept()
        conn.setblocking(False)

        # Drop old client
        if self.data_client is not None:
            self._disconnect_data_client()

        self.data_client = conn
        logger.info(f"Data client connected for {self.name}")

        # Send ring buffer contents to new client (best-effort; may drop if buffer full)
        buf = self.ring.read_all()
        if buf:
            try:
                conn.sendall(buf)
            except BlockingIOError:
                logger.debug(f"Initial buffer send would block for {self.name}, client gets live only")
            except (BrokenPipeError, ConnectionResetError, OSError):
                self._disconnect_data_client()
                return

        self.sel.register(conn, selectors.EVENT_READ, self._on_data_client_read)

    def _on_data_client_read(self, conn):
        """Data from server → PTY master."""
        try:
            data = conn.recv(65536)
            if not data:
                logger.info(f"Data client disconnected (EOF) for {self.name}")
                self._disconnect_data_client()
                return
            os.write(self.master_fd, data)
        except (BlockingIOError, OSError):
            pass
        except (BrokenPipeError, ConnectionResetError):
            self._disconnect_data_client()

    def _disconnect_data_client(self):
        if self.data_client is not None:
            try:
                self.sel.unregister(self.data_client)
            except (KeyError, ValueError):
                pass
            try:
                self.data_client.close()
            except OSError:
                pass
            self.data_client = None

    # -- Control socket ------------------------------------------------------

    def _on_ctrl_accept(self, srv_sock):
        conn, _ = srv_sock.accept()
        conn.setblocking(False)

        # Drop old control client
        if self.ctrl_client is not None:
            self._disconnect_ctrl_client()

        self.ctrl_client = conn
        self._ctrl_buf = b""
        self.sel.register(conn, selectors.EVENT_READ, self._on_ctrl_client_read)
        logger.info(f"Control client connected for {self.name}")

    def _on_ctrl_client_read(self, conn):
        try:
            data = conn.recv(4096)
            if not data:
                self._disconnect_ctrl_client()
                return
            self._ctrl_buf += data
            # Process complete lines
            while b"\n" in self._ctrl_buf:
                line, self._ctrl_buf = self._ctrl_buf.split(b"\n", 1)
                self._handle_ctrl_message(conn, line)
        except (BlockingIOError, OSError):
            pass
        except (BrokenPipeError, ConnectionResetError):
            self._disconnect_ctrl_client()

    def _handle_ctrl_message(self, conn: socket.socket, line: bytes):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self._ctrl_respond(conn, {"error": "invalid JSON"})
            return

        msg_type = msg.get("type")

        if msg_type == "resize":
            cols = msg.get("cols", self.cols)
            rows = msg.get("rows", self.rows)
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
                self.cols = cols
                self.rows = rows
                self._ctrl_respond(conn, {"ok": True})
            except Exception as e:
                self._ctrl_respond(conn, {"error": str(e)})

        elif msg_type == "status":
            self._ctrl_respond(conn, {
                "pid": self.child_pid,
                "alive": self._child_alive,
                "uptime": time.monotonic() - self.start_time,
                "cols": self.cols,
                "rows": self.rows,
            })

        elif msg_type == "buffer":
            buf = self.ring.read_all()
            self._ctrl_respond(conn, {
                "data": base64.b64encode(buf).decode("ascii"),
                "length": len(buf),
            })

        elif msg_type == "kill":
            self._ctrl_respond(conn, {"ok": True})
            self._kill_child()
            self._running = False

        else:
            self._ctrl_respond(conn, {"error": f"unknown type: {msg_type}"})

    def _ctrl_respond(self, conn: socket.socket, msg: dict):
        try:
            conn.sendall(json.dumps(msg).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _disconnect_ctrl_client(self):
        if self.ctrl_client is not None:
            try:
                self.sel.unregister(self.ctrl_client)
            except (KeyError, ValueError):
                pass
            try:
                self.ctrl_client.close()
            except OSError:
                pass
            self.ctrl_client = None
            self._ctrl_buf = b""

    # -- Cleanup -------------------------------------------------------------

    def _kill_child(self):
        if self._child_alive:
            try:
                os.killpg(os.getpgid(self.child_pid), signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass
            self._child_alive = False

    def _cleanup(self):
        logger.info(f"Relay {self.name} cleaning up")

        self._disconnect_data_client()
        self._disconnect_ctrl_client()

        # Close server sockets
        for srv in (self.data_srv, self.ctrl_srv):
            if srv:
                try:
                    self.sel.unregister(srv)
                except (KeyError, ValueError):
                    pass
                srv.close()

        # Close PTY master
        try:
            self.sel.unregister(self.master_fd)
        except (KeyError, ValueError):
            pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass

        self.sel.close()

        # Remove socket files and pid file
        for p in (self.data_sock_path, self.ctrl_sock_path, self.pid_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass

        logger.info(f"Relay {self.name} exited")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PTY relay process")
    parser.add_argument("--name", required=True, help="Relay/session name")
    parser.add_argument("--cmd", required=True, help="Command to run")
    parser.add_argument("--cwd", required=True, help="Working directory")
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--socket-dir", required=True, help="Directory for Unix sockets")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [pty_relay:{args.name}] %(levelname)s %(message)s",
    )

    relay = PtyRelay(
        name=args.name,
        cmd=args.cmd,
        cwd=args.cwd,
        cols=args.cols,
        rows=args.rows,
        socket_dir=args.socket_dir,
    )
    relay.start()


if __name__ == "__main__":
    main()
