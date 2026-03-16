"""Visual streaming via neko/VNC for web process previews."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Optional
from datetime import datetime

from .db.models import VNCSession, VNCStatus
from .db.repositories import get_vnc_repo

logger = logging.getLogger(__name__)

CDP_PORT = 9222

# Backward-compat: external code may import VNCSession/VNCStatus from here
__all__ = ["VNCManager", "VNCSession", "VNCStatus"]


class VNCManager:
    """Manages neko browser containers for visual streaming."""
    
    def __init__(self):
        self._sessions: dict[str, VNCSession] = {}
        self._repo = get_vnc_repo()
        self._load_sessions()

    def _load_sessions(self):
        """Load persisted sessions from the database."""
        for session in self._repo.list():
            # Check if container is still running
            if session.container_id and self._is_container_running(session.container_id):
                session.status = VNCStatus.RUNNING
            else:
                session.status = VNCStatus.STOPPED
                session.container_id = None
            self._repo.upsert(session)
            self._sessions[session.id] = session
    
    def _is_container_running(self, container_id: str) -> bool:
        """Check if Docker container is running."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0 and "true" in result.stdout.lower()
        except Exception:
            return False
    
    def _is_docker_available(self) -> tuple[bool, Optional[str]]:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return True, None
            return False, "Docker daemon not responding"
        except FileNotFoundError:
            return False, "Docker not installed"
        except subprocess.TimeoutExpired:
            return False, "Docker daemon timeout"
        except Exception as e:
            return False, str(e)
    
    def _find_available_port(self, start: int = 8080) -> int:
        """Find an available port."""
        import socket
        
        for port in range(start, start + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', port))
                    return port
            except OSError:
                continue
        raise RuntimeError("No available ports found")
    
    def create_session(
        self,
        process_id: str,
        target_url: str,
        preferred_vnc_port: Optional[int] = None,
    ) -> VNCSession:
        """Create a new VNC session for a process.
        
        Args:
            process_id: ID of the process to visualize
            target_url: URL to open in the browser (e.g., http://localhost:3000)
            preferred_vnc_port: Preferred port for VNC viewer (optional)
        """
        # Check if session already exists — clean up stale ones
        existing = self.get_by_process(process_id)
        if existing:
            if existing.status == VNCStatus.RUNNING:
                return existing
            # Remove stale stopped/failed session before creating a new one
            self.delete_session(existing.id)
        
        # Check Docker availability
        docker_ok, docker_error = self._is_docker_available()
        if not docker_ok:
            session = VNCSession(
                id=f"vnc-{process_id}",
                process_id=process_id,
                target_url=target_url,
                vnc_port=0,
                web_port=0,
                status=VNCStatus.FAILED,
                error=f"Docker unavailable: {docker_error}",
            )
            self._repo.upsert(session)
            self._sessions[session.id] = session
            return session
        
        # Find available ports
        vnc_port = preferred_vnc_port or self._find_available_port(8090)
        web_port = self._find_available_port(vnc_port + 1)
        
        session = VNCSession(
            id=f"vnc-{process_id}",
            process_id=process_id,
            target_url=target_url,
            vnc_port=vnc_port,
            web_port=web_port,
            status=VNCStatus.STARTING,
            started_at=datetime.now(),
        )
        
        try:
            # Use Neko V3 - has ARM64 support and uses HTTP (no cert issues)
            # WebRTC-based streaming, embeddable in iframe
            image = "ghcr.io/m1k1o/neko/chromium"
            container_name = f"rdc-vnc-{process_id}"
            
            # Remove existing container if any
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=10
            )
            
            # Start new container
            # Port 8080 = Neko web interface (HTTP, no SSL issues)
            # UDP ports 52000-52100 for WebRTC, NAT1TO1 for Docker on macOS
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "--shm-size=2gb",
                "--cap-add=SYS_ADMIN",
                "-p", f"{vnc_port}:8080",
                "-p", "52000-52100:52000-52100/udp",
                "--add-host=host.docker.internal:host-gateway",
                "-e", "NEKO_SCREEN=1280x720@30",
                "-e", "NEKO_PASSWORD=neko",
                "-e", "NEKO_PASSWORD_ADMIN=admin",
                "-e", "NEKO_EPR=52000-52100",
                "-e", "NEKO_NAT1TO1=127.0.0.1",
                image,
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to start container: {result.stderr}")
            
            container_id = result.stdout.strip()
            session.container_id = container_id
            session.status = VNCStatus.RUNNING
            
            # Wait for container to be ready, then launch Chrome
            self._launch_browser(container_id, target_url)
            
        except Exception as e:
            session.status = VNCStatus.FAILED
            session.error = str(e)
        
        self._repo.upsert(session)
        self._sessions[session.id] = session
        return session
    
    def _launch_browser(self, container_id: str, url: str, retries: int = 5):
        """Launch Chromium with URL and CDP enabled inside the Neko container."""
        import threading
        
        def _launch():
            for attempt in range(retries):
                time.sleep(5)
                try:
                    result = subprocess.run(
                        [
                            "docker", "exec", container_id,
                            "sh", "-c",
                            f"DISPLAY=:99.0 /usr/bin/chromium --no-sandbox --disable-gpu "
                            f"--disable-extensions --disable-infobars --test-type "
                            f"--remote-debugging-port={CDP_PORT} "
                            f"'{url}' &"
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                    if result.returncode == 0:
                        return
                except Exception:
                    pass
        
        thread = threading.Thread(target=_launch, daemon=True)
        thread.start()
    
    def stop_session(self, session_id: str) -> bool:
        """Stop a VNC session."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        
        if session.container_id:
            try:
                subprocess.run(
                    ["docker", "stop", session.container_id],
                    capture_output=True,
                    timeout=10
                )
                subprocess.run(
                    ["docker", "rm", session.container_id],
                    capture_output=True,
                    timeout=10
                )
            except Exception:
                pass
        
        session.status = VNCStatus.STOPPED
        session.container_id = None
        self._repo.upsert(session)
        return True
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a VNC session."""
        self.stop_session(session_id)

        if session_id in self._sessions:
            self._repo.delete(session_id)
            del self._sessions[session_id]
            return True
        return False
    
    def get_session(self, session_id: str) -> Optional[VNCSession]:
        """Get a session by ID."""
        session = self._sessions.get(session_id)
        if session:
            # Refresh status
            if session.container_id and session.status == VNCStatus.RUNNING:
                if not self._is_container_running(session.container_id):
                    session.status = VNCStatus.STOPPED
                    session.container_id = None
                    self._repo.upsert(session)
        return session
    
    def get_by_process(self, process_id: str) -> Optional[VNCSession]:
        """Get session for a specific process."""
        for session in self._sessions.values():
            if session.process_id == process_id:
                return self.get_session(session.id)  # Refresh status
        return None
    
    def list_sessions(self) -> list[VNCSession]:
        """List all sessions, refreshing container status."""
        for session in list(self._sessions.values()):
            if session.container_id and session.status == VNCStatus.RUNNING:
                if not self._is_container_running(session.container_id):
                    session.status = VNCStatus.STOPPED
                    session.container_id = None
                    self._repo.upsert(session)
        return list(self._sessions.values())
    
    def list_active(self) -> list[VNCSession]:
        """List only running sessions."""
        return [s for s in self._sessions.values() if s.status == VNCStatus.RUNNING]
    
    def restart_session(self, session_id: str) -> VNCSession:
        """Restart a session."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        # Stop current session
        self.stop_session(session_id)
        
        # Create new session with same parameters
        return self.create_session(
            process_id=session.process_id,
            target_url=session.target_url,
            preferred_vnc_port=session.vnc_port,
        )
    
    def cleanup_stopped(self):
        """Remove all stopped sessions."""
        to_delete = [
            sid for sid, session in self._sessions.items()
            if session.status == VNCStatus.STOPPED
        ]
        for sid in to_delete:
            self.delete_session(sid)
    
    def stop_all(self):
        """Stop all running sessions."""
        for session_id in list(self._sessions.keys()):
            self.stop_session(session_id)
    
    def capture_screenshot(self, session_id: str, full_page: bool = False) -> Optional[bytes]:
        """Capture a screenshot from a VNC session.
        
        Uses CDP on the already-running browser (preferred) with scrot as fallback.
        """
        session = self.get_session(session_id)
        if not session or session.status != VNCStatus.RUNNING or not session.container_id:
            return None
        
        # Try CDP first (works for both full_page and viewport)
        try:
            ws_url = self._get_cdp_ws_url(session.container_id)
            if ws_url:
                result = self._cdp_capture(session.container_id, ws_url, full_page=full_page)
                if result:
                    return result
        except Exception as e:
            logger.debug(f"CDP capture failed, falling back to scrot: {e}")
        
        # Fallback to scrot (viewport only)
        return self._capture_viewport(session)
    
    def _capture_viewport(self, session: VNCSession) -> Optional[bytes]:
        """Capture the visible X11 desktop viewport using scrot."""
        # Try scrot first (most reliable in Neko containers)
        try:
            result = subprocess.run(
                [
                    "docker", "exec", session.container_id,
                    "sh", "-c",
                    "DISPLAY=:99.0 scrot -o /tmp/screenshot.png && cat /tmp/screenshot.png"
                ],
                capture_output=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout and len(result.stdout) > 100:
                return result.stdout
        except Exception:
            pass
        
        # Fallback to import (ImageMagick) if available
        try:
            result = subprocess.run(
                [
                    "docker", "exec", session.container_id,
                    "sh", "-c",
                    "DISPLAY=:99.0 import -window root png:-"
                ],
                capture_output=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout and len(result.stdout) > 100:
                return result.stdout
        except Exception:
            pass
        
        return None
    
    def _get_cdp_ws_url(self, container_id: str) -> Optional[str]:
        """Get the WebSocket debugger URL for the first page target."""
        result = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c",
             f"curl -s http://127.0.0.1:{CDP_PORT}/json 2>/dev/null"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        for target in json.loads(result.stdout):
            if target.get("type") == "page":
                return target.get("webSocketDebuggerUrl")
        return None

    def _cdp_capture(self, container_id: str, ws_url: str, full_page: bool = True) -> Optional[bytes]:
        """Drive CDP over a raw WebSocket (stdlib only, no pip deps).

        Connects to the browser's CDP WebSocket, measures the document,
        resizes the viewport to cover the full page, and captures a PNG.
        Resets the viewport override afterwards so the visible browser
        is unaffected.
        """
        # This script runs inside the container using only python3 stdlib.
        # It implements a minimal WebSocket client and sends CDP commands.
        cdp_script = r'''
import json, sys, base64, socket, os, struct

def ws_connect(url):
    from urllib.parse import urlparse
    p = urlparse(url)
    host, port = p.hostname, p.port or 80
    s = socket.create_connection((host, port), timeout=15)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((
        f"GET {p.path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    return s

def ws_send(s, data):
    payload = data.encode()
    frame = bytearray([0x81])
    l = len(payload)
    if l < 126:
        frame.append(0x80 | l)
    elif l < 65536:
        frame.append(0x80 | 126)
        frame += struct.pack(">H", l)
    else:
        frame.append(0x80 | 127)
        frame += struct.pack(">Q", l)
    mask = os.urandom(4)
    frame += mask
    frame += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    s.sendall(frame)

def ws_recv(s):
    def read_n(n):
        buf = b""
        while len(buf) < n:
            c = s.recv(n - len(buf))
            if not c: raise ConnectionError
            buf += c
        return buf
    h = read_n(2)
    length = h[1] & 0x7f
    if length == 126:
        length = struct.unpack(">H", read_n(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", read_n(8))[0]
    if h[1] & 0x80:
        mask = read_n(4)
        data = read_n(length)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    else:
        data = read_n(length)
    return data.decode(errors="replace")

ws_url = sys.argv[1]
full_page = sys.argv[2] == "1" if len(sys.argv) > 2 else True
s = ws_connect(ws_url)
mid = 0

def cdp(method, params=None):
    global mid; mid += 1
    msg = {"id": mid, "method": method}
    if params: msg["params"] = params
    ws_send(s, json.dumps(msg))
    while True:
        r = json.loads(ws_recv(s))
        if r.get("id") == mid:
            return r.get("result", {})

if full_page:
    metrics = cdp("Page.getLayoutMetrics")
    cs = metrics.get("contentSize", metrics.get("cssContentSize", {}))
    w = max(int(cs.get("width", 1280)), 800)
    h = max(int(cs.get("height", 720)), 400)
    h = min(h, 32000)
    cdp("Emulation.setDeviceMetricsOverride", {
        "width": w, "height": h, "deviceScaleFactor": 1, "mobile": False
    })
    import time; time.sleep(0.3)

shot = cdp("Page.captureScreenshot", {
    "format": "png", "captureBeyondViewport": full_page
})

if full_page:
    cdp("Emulation.clearDeviceMetricsOverride")

data = shot.get("data", "")
if data:
    sys.stdout.buffer.write(base64.b64decode(data))
    sys.exit(0)
sys.exit(1)
'''

        fp_flag = "1" if full_page else "0"
        result = subprocess.run(
            ["docker", "exec", container_id, "python3", "-c", cdp_script, ws_url, fp_flag],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and result.stdout and len(result.stdout) > 100:
            return result.stdout

        if result.stderr:
            logger.debug(f"CDP capture stderr: {result.stderr[:300]}")
        return None


# Global VNC manager
_vnc_manager: Optional[VNCManager] = None


def get_vnc_manager() -> VNCManager:
    """Get the global VNC manager."""
    global _vnc_manager
    if _vnc_manager is None:
        _vnc_manager = VNCManager()
    return _vnc_manager
