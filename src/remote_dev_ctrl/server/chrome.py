"""Local Chrome/Chromium process lifecycle manager.

Replaces Docker-based browserless containers with a locally-managed
Chrome subprocess using --remote-debugging-port for CDP access.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Optional

import httpx

from .config import get_rdc_home

logger = logging.getLogger(__name__)

# CDP port range for local Chrome instances (separate from legacy Docker 9500+)
CHROME_PORT_MIN = 9222
CHROME_PORT_MAX = 9322


def find_chrome_binary() -> Optional[str]:
    """Discover Chrome/Chromium binary on the current platform.

    Priority:
    1. CHROME_PATH environment variable
    2. Platform-specific well-known locations
    3. PATH lookup
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    system = platform.system()

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c

    elif system == "Linux":
        candidates = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        ]
        for c in candidates:
            found = shutil.which(c)
            if found:
                return found

    elif system == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c

    # Fallback: PATH lookup
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    return None


class ChromeProcess:
    """Manages a single Chrome subprocess with remote debugging enabled."""

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self.port: int = 0
        self.user_data_dir: Optional[Path] = None
        self._headless: bool = True

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    def start(
        self,
        port: int,
        headless: bool = True,
        user_data_dir: Optional[Path] = None,
        chrome_path: Optional[str] = None,
    ) -> None:
        """Launch Chrome with remote debugging on the given port.

        Args:
            port: CDP port to listen on.
            headless: Run in headless mode (default True).
            user_data_dir: Chrome profile directory. Auto-created if None.
            chrome_path: Explicit path to Chrome binary. Auto-detected if None.
        """
        binary = chrome_path or find_chrome_binary()
        if not binary:
            raise RuntimeError(
                "Chrome/Chromium not found. Install Chrome or set CHROME_PATH."
            )

        self.port = port
        self._headless = headless

        if user_data_dir is None:
            user_data_dir = get_rdc_home() / "chrome-profiles" / f"session-{port}"
        self.user_data_dir = user_data_dir
        user_data_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            binary,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
            "--disable-default-apps",
            "--mute-audio",
            "--no-sandbox",
        ]

        if headless:
            cmd.append("--headless=new")

        # Start with about:blank — targets are created via CDP
        cmd.append("about:blank")

        logger.info(f"Starting Chrome on port {port}: {binary} (headless={headless})")

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if os.name != "nt" else None,
        )

        logger.info(f"Chrome started, PID={self._process.pid}")

    async def wait_for_ready(self, timeout: float = 15.0) -> None:
        """Poll the CDP /json/version endpoint until Chrome is responsive."""
        import time

        deadline = time.time() + timeout
        url = f"http://localhost:{self.port}/json/version"

        while time.time() < deadline:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, timeout=2)
                    if resp.status_code == 200:
                        data = resp.json()
                        logger.info(
                            f"Chrome ready on port {self.port}: "
                            f"{data.get('Browser', 'unknown')}"
                        )
                        return
            except Exception:
                pass
            await asyncio.sleep(0.5)

        raise TimeoutError(f"Chrome on port {self.port} not ready after {timeout}s")

    def stop(self) -> None:
        """Stop the Chrome process. SIGTERM first, SIGKILL after 5s."""
        if not self._process:
            return

        pid = self._process.pid
        logger.info(f"Stopping Chrome PID={pid}")

        try:
            if os.name != "nt":
                # Kill the process group to clean up all child processes
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                self._process.terminate()
        except (ProcessLookupError, OSError):
            return

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(f"Chrome PID={pid} did not exit, sending SIGKILL")
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                else:
                    self._process.kill()
            except (ProcessLookupError, OSError):
                pass

        self._process = None

    def is_alive(self) -> bool:
        """Check if the Chrome subprocess is still running."""
        if not self._process:
            return False
        return self._process.poll() is None

    async def is_responsive(self) -> bool:
        """Check if Chrome is both alive and responding to CDP."""
        if not self.is_alive():
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://localhost:{self.port}/json/version", timeout=2
                )
                return resp.status_code == 200
        except Exception:
            return False


def find_available_chrome_port() -> int:
    """Find an available port in the Chrome CDP port range."""
    import socket

    for port in range(CHROME_PORT_MIN, CHROME_PORT_MAX):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports in range {CHROME_PORT_MIN}-{CHROME_PORT_MAX}")
