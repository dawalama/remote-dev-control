"""Caddy reverse proxy manager for subdomain-based preview URLs.

Each dev server process gets a subdomain like `frontend.preview.yourdomain.com`
so apps think they're at root `/` — no path prefix conflicts.

Caddy is managed via its admin API: we keep the full JSON config in memory
and POST to `/load` on every change (atomic, zero-downtime reloads).
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from .config import CaddyConfig, get_rdc_home

logger = logging.getLogger(__name__)

CADDY_VERSION = "2.9.1"


class CaddyManager:
    """Manages a Caddy subprocess and its routes via the admin API."""

    def __init__(self, config: CaddyConfig):
        self._config = config
        self._process: Optional[subprocess.Popen] = None
        self._routes: dict[str, _Route] = {}  # process_id -> route info
        self._binary: Optional[str] = None
        self.available = False
        self._client = httpx.AsyncClient(timeout=5.0)

    def _check_binary(self) -> bool:
        """Find caddy in PATH or in ~/.rdc/bin/."""
        # Check PATH first
        found = shutil.which("caddy")
        if found:
            self._binary = found
            self.available = True
            return True

        # Check ~/.rdc/bin/
        local_bin = get_rdc_home() / "bin" / "caddy"
        if local_bin.exists() and os.access(str(local_bin), os.X_OK):
            self._binary = str(local_bin)
            self.available = True
            logger.info("Using Caddy from %s", self._binary)
            return True

        self.available = False
        return False

    async def _ensure_binary(self) -> bool:
        """Check for caddy binary, auto-download if missing."""
        if self._check_binary():
            return True

        logger.info("Caddy not found, downloading v%s...", CADDY_VERSION)
        try:
            path = await _download_caddy(CADDY_VERSION)
            self._binary = str(path)
            self.available = True
            logger.info("Caddy installed to %s", path)
            return True
        except Exception:
            logger.exception("Failed to download Caddy")
            self.available = False
            return False

    @property
    def admin_url(self) -> str:
        return f"http://localhost:{self._config.admin_port}"

    async def start(self) -> bool:
        """Start Caddy subprocess and load initial config."""
        if not await self._ensure_binary():
            return False

        # Start Caddy with admin API on configured port
        cmd = [
            self._binary, "run",
            "--config", "-",  # read JSON config from stdin
        ]
        # We start with a minimal config; the real config is POSTed via /load
        initial_config = self._build_config()

        import json
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Feed initial config via stdin then close
            self._process.stdin.write(json.dumps(initial_config).encode())
            self._process.stdin.close()
        except Exception:
            logger.exception("Failed to start Caddy")
            self.available = False
            return False

        # Wait a moment for Caddy to start
        await asyncio.sleep(0.5)

        if self._process.poll() is not None:
            stderr = self._process.stderr.read().decode() if self._process.stderr else ""
            logger.error("Caddy exited immediately: %s", stderr)
            self.available = False
            self._process = None
            return False

        logger.info(
            "Caddy started (PID %d) on :%d, admin on :%d",
            self._process.pid,
            self._config.listen_port,
            self._config.admin_port,
        )
        return True

    async def stop(self):
        """Stop the Caddy subprocess."""
        if self._process is None:
            return

        pid = self._process.pid
        try:
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        except Exception:
            logger.exception("Error stopping Caddy (PID %d)", pid)
        finally:
            self._process = None
            logger.info("Caddy stopped")

        await self._client.aclose()

    async def add_route(self, process_id: str, subdomain: str, target_port: int):
        """Add a route for a process and reload Caddy config."""
        self._routes[process_id] = _Route(
            subdomain=subdomain,
            target_port=target_port,
        )
        logger.info("Adding Caddy route: %s.%s -> localhost:%d", subdomain, self._config.base_domain, target_port)
        await self._reload_config()

    async def remove_route(self, process_id: str):
        """Remove a route for a process and reload Caddy config."""
        route = self._routes.pop(process_id, None)
        if route:
            logger.info("Removing Caddy route: %s.%s", route.subdomain, self._config.base_domain)
            await self._reload_config()

    def get_preview_url(self, process_id: str) -> Optional[str]:
        """Get the public preview URL for a process, or None."""
        route = self._routes.get(process_id)
        if route is None:
            return None
        return f"https://{route.subdomain}.{self._config.base_domain}"

    def list_routes(self) -> list[dict]:
        """Return current route table."""
        return [
            {
                "process_id": pid,
                "subdomain": r.subdomain,
                "target_port": r.target_port,
                "url": f"https://{r.subdomain}.{self._config.base_domain}",
            }
            for pid, r in self._routes.items()
        ]

    def _build_config(self) -> dict:
        """Build the full Caddy JSON config."""
        routes = []

        # RDC dashboard route — needs long timeouts for LLM orchestrator calls
        # and proper WebSocket support for /ws/* paths
        routes.append({
            "match": [{"host": [self._config.rdc_domain]}],
            "handle": [{
                "handler": "reverse_proxy",
                "upstreams": [{"dial": f"localhost:{8420}"}],
                "transport": {
                    "protocol": "http",
                    "read_timeout": 120_000_000_000,   # 120s in nanoseconds
                    "write_timeout": 120_000_000_000,
                },
                "headers": {
                    "request": {
                        "set": {
                            "X-Forwarded-Proto": ["{http.request.scheme}"],
                        },
                    },
                },
            }],
        })

        # Dynamic process routes
        for route in self._routes.values():
            host = f"{route.subdomain}.{self._config.base_domain}"
            routes.append({
                "match": [{"host": [host]}],
                "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": f"localhost:{route.target_port}"}],
                    "transport": {
                        "protocol": "http",
                        "read_timeout": 120_000_000_000,
                        "write_timeout": 120_000_000_000,
                    },
                    # Stream responses immediately (needed for chunked/SSE)
                    "flush_interval": -1,
                    # Rewrite Host header so dev servers (Vite, Next.js, etc.)
                    # accept the request instead of rejecting the subdomain host.
                    "headers": {
                        "request": {
                            "set": {
                                "Host": [f"localhost:{route.target_port}"],
                                "X-Forwarded-Proto": ["{http.request.scheme}"],
                                "X-Forwarded-Host": [host],
                            },
                        },
                    },
                }],
            })

        return {
            "admin": {
                "listen": f"localhost:{self._config.admin_port}",
            },
            "apps": {
                "http": {
                    "servers": {
                        "preview": {
                            "listen": [f":{self._config.listen_port}"],
                            "routes": routes,
                            # No automatic HTTPS — Cloudflare handles TLS
                            "automatic_https": {
                                "disable": True,
                            },
                        },
                    },
                },
            },
        }

    async def _reload_config(self):
        """POST full config to Caddy's /load endpoint."""
        if self._process is None or self._process.poll() is not None:
            logger.warning("Caddy not running, skipping config reload")
            return

        import json
        config = self._build_config()
        try:
            resp = await self._client.post(
                f"{self.admin_url}/load",
                content=json.dumps(config),
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.error("Caddy config reload failed (%d): %s", resp.status_code, resp.text)
            else:
                logger.debug("Caddy config reloaded successfully")
        except Exception:
            logger.exception("Failed to POST config to Caddy admin API")


class _Route:
    __slots__ = ("subdomain", "target_port")

    def __init__(self, subdomain: str, target_port: int):
        self.subdomain = subdomain
        self.target_port = target_port


def sanitize_subdomain(project: str, name: str) -> str:
    """Build a subdomain from project + process name.

    Result is lowercase, alphanumeric + hyphens, no leading/trailing hyphens.
    """
    raw = f"{project}-{name}"
    # Replace non-alphanumeric with hyphens, collapse runs, strip edges
    cleaned = re.sub(r"[^a-z0-9-]", "-", raw.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "unknown"


# ── Auto-download ────────────────────────────────────────────────────────

async def _download_caddy(version: str) -> Path:
    """Download Caddy binary from GitHub releases into ~/.rdc/bin/."""
    system = platform.system().lower()   # darwin, linux
    machine = platform.machine().lower() # x86_64, arm64, aarch64

    # Map to Caddy's naming convention
    if system == "darwin":
        os_name = "mac"
    elif system == "linux":
        os_name = "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    ext = "tar.gz" if system != "windows" else "zip"
    filename = f"caddy_{version}_{os_name}_{arch}.{ext}"
    url = f"https://github.com/caddyserver/caddy/releases/download/v{version}/{filename}"

    bin_dir = get_rdc_home() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    dest = bin_dir / "caddy"
    archive_path = bin_dir / filename

    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        logger.info("Downloading %s", url)
        resp = await client.get(url)
        resp.raise_for_status()
        archive_path.write_bytes(resp.content)

    # Extract the caddy binary from the archive
    try:
        if ext == "tar.gz":
            with tarfile.open(str(archive_path), "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name == "caddy" or member.name.endswith("/caddy"):
                        member.name = "caddy"
                        tar.extract(member, path=str(bin_dir))
                        break
        elif ext == "zip":
            with zipfile.ZipFile(str(archive_path)) as zf:
                for name in zf.namelist():
                    if name == "caddy" or name == "caddy.exe":
                        data = zf.read(name)
                        dest.write_bytes(data)
                        break
    finally:
        archive_path.unlink(missing_ok=True)

    # Make executable
    dest.chmod(0o755)

    if not dest.exists():
        raise RuntimeError("Caddy binary not found in downloaded archive")

    return dest


# ── Singleton ────────────────────────────────────────────────────────────

_manager: Optional[CaddyManager] = None


def get_caddy_manager() -> Optional[CaddyManager]:
    """Get the global CaddyManager instance (None if not initialized)."""
    return _manager


def set_caddy_manager(mgr: Optional[CaddyManager]):
    """Set the global CaddyManager instance."""
    global _manager
    _manager = mgr
