"""PinchTab browser automation client — config + async httpx wrapper.

PinchTab is a Go binary HTTP server providing browser automation via REST API.
System-level singleton, auto-started on first use via ProcessManager.

v0.7.8+ runs in dashboard mode — manages profiles with separate Chrome instances.
The dashboard proxies standard API calls (/snapshot, /navigate, etc.) to the
active instance.
"""

import json as _json
import logging
import os
import shutil
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — JSON file at ~/.rdc/pinchtab.json
# ---------------------------------------------------------------------------

DEFAULT_PINCHTAB_CONFIG = {
    "enabled": True,
    "port": 9867,
    "binary": "",       # empty = auto-detect
    "headless": True,
}


def _pinchtab_config_path() -> Path:
    from .config import get_rdc_home
    return get_rdc_home() / "pinchtab.json"


def load_pinchtab_config() -> dict:
    """Load PinchTab config from disk."""
    path = _pinchtab_config_path()
    if path.exists():
        try:
            with open(path) as f:
                stored = _json.load(f)
            return {**DEFAULT_PINCHTAB_CONFIG, **stored}
        except Exception:
            pass
    return dict(DEFAULT_PINCHTAB_CONFIG)


def save_pinchtab_config(config: dict) -> dict:
    """Save PinchTab config to disk. Returns the saved config."""
    path = _pinchtab_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_PINCHTAB_CONFIG, **config}
    with open(path, "w") as f:
        _json.dump(merged, f, indent=2)
    return merged


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def _resolve_binary() -> str:
    """Resolve pinchtab binary path: config → env → common locations → PATH."""
    cfg = load_pinchtab_config()
    if cfg.get("binary"):
        return cfg["binary"]
    env = os.environ.get("PINCHTAB_BINARY")
    if env:
        return env
    for candidate in ["/opt/homebrew/bin/pinchtab", "/usr/local/bin/pinchtab"]:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("pinchtab")
    if found:
        return found
    return "pinchtab"  # fallback, will fail at start time


# ---------------------------------------------------------------------------
# Health check with TTL cache
# ---------------------------------------------------------------------------

_health_cache: dict[str, Any] = {"result": None, "ts": 0.0}
_HEALTH_TTL = 30.0  # seconds


def check_health(port: int | None = None) -> bool:
    """Quick urllib health check with 30s TTL cache."""
    now = time.monotonic()
    if now - _health_cache["ts"] < _HEALTH_TTL and _health_cache["result"] is not None:
        return _health_cache["result"]
    if port is None:
        port = load_pinchtab_config().get("port", 9867)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", method="GET")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status != 200:
                _health_cache["result"] = False
                _health_cache["ts"] = now
                return False
            data = _json.loads(resp.read())
            # v0.7.8 dashboard mode: {"mode":"dashboard","status":"ok"}
            # v0.7.6 headless mode: {"cdp":"...","status":"ok","tabs":N}
            ok = data.get("status") == "ok" or data.get("mode") == "dashboard"
        _health_cache["result"] = ok
        _health_cache["ts"] = now
        return ok
    except Exception:
        _health_cache["result"] = False
        _health_cache["ts"] = now
        return False


def invalidate_health_cache():
    """Force next health check to hit the server."""
    _health_cache["ts"] = 0.0


# ---------------------------------------------------------------------------
# Async httpx client
# ---------------------------------------------------------------------------

class PinchTabClient:
    """Async client wrapping PinchTab HTTP API."""

    def __init__(self, port: int = 9867):
        self._port = port
        self._base = f"http://127.0.0.1:{port}"

    async def _get(self, path: str, params: dict | None = None) -> Any:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._base}{path}", params=params)
                if r.status_code >= 400:
                    try:
                        body = r.json()
                    except Exception:
                        body = {}
                    return {"error": body.get("error", f"http_{r.status_code}"), "status": r.status_code, "detail": body.get("detail", r.text[:200])}
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            invalidate_health_cache()
            return {"error": "connection_error", "detail": str(e)}

    async def _post(self, path: str, body: dict | None = None) -> Any:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(f"{self._base}{path}", json=body or {})
                if r.status_code >= 400:
                    try:
                        resp_body = r.json()
                    except Exception:
                        resp_body = {}
                    return {"error": resp_body.get("error", f"http_{r.status_code}"), "status": r.status_code, "detail": resp_body.get("detail", r.text[:200])}
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            invalidate_health_cache()
            return {"error": "connection_error", "detail": str(e)}

    async def health(self) -> bool:
        try:
            data = await self._get("/health")
            if not isinstance(data, dict):
                return False
            return data.get("status") == "ok" or data.get("mode") == "dashboard"
        except Exception:
            return False

    async def navigate(self, url: str, tab_id: str | None = None) -> dict:
        body: dict[str, Any] = {"url": url}
        if tab_id:
            body["tabId"] = tab_id
        return await self._post("/navigate", body)

    async def snapshot(self, tab_id: str | None = None) -> dict:
        params = {"tabId": tab_id} if tab_id else None
        return await self._get("/snapshot", params)

    async def action(self, action_type: str, ref: str | int, value: str | None = None, tab_id: str | None = None) -> dict:
        body: dict[str, Any] = {"kind": action_type, "ref": str(ref)}
        # Map value to the correct field per action kind
        if value is not None:
            if action_type == "type" or action_type == "humanType":
                body["text"] = value
            elif action_type == "press":
                body["key"] = value
            elif action_type == "select":
                body["value"] = value
            else:
                body["value"] = value
        if tab_id:
            body["tabId"] = tab_id
        return await self._post("/action", body)

    async def text(self, tab_id: str | None = None) -> dict:
        params = {"tabId": tab_id} if tab_id else None
        return await self._get("/text", params)

    async def tabs(self) -> dict:
        return await self._get("/tabs")

    async def close_tab(self, tab_id: str) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(f"{self._base}/tabs/{tab_id}/close", json={})
                if r.status_code == 404:
                    return {"error": "not_found", "detail": "Tab not found"}
                r.raise_for_status()
                return r.json()
        except (httpx.ReadTimeout, httpx.ConnectError):
            # Closing the last tab can kill the Chrome instance and hang
            invalidate_health_cache()
            return {"closed": True, "warning": "Connection lost — Chrome instance may have stopped"}

    async def screenshot(self, tab_id: str | None = None) -> dict:
        params = {"tabId": tab_id} if tab_id else None
        return await self._get("/screenshot", params)

    async def evaluate(self, expression: str, tab_id: str | None = None) -> dict:
        body: dict[str, Any] = {"expression": expression}
        if tab_id:
            body["tabId"] = tab_id
        return await self._post("/evaluate", body)

    async def find(self, description: str, tab_id: str | None = None) -> dict:
        """NL element find → POST /tabs/{tabId}/find."""
        tid = tab_id or "default"
        return await self._post(f"/tabs/{tid}/find", {"description": description})

    async def snapshot_filtered(self, filter: str = "interactive", compact: bool = True, tab_id: str | None = None) -> dict:
        """Get filtered snapshot → GET /snapshot?filter=interactive&compact=true."""
        params: dict[str, str] = {}
        if filter:
            params["filter"] = filter
        if compact:
            params["compact"] = "true"
        if tab_id:
            params["tabId"] = tab_id
        return await self._get("/snapshot", params)

    async def pdf(self, tab_id: str | None = None) -> bytes:
        """Get page PDF → GET /tabs/{tabId}/pdf. Returns raw PDF bytes."""
        import httpx
        tid = tab_id or "default"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self._base}/tabs/{tid}/pdf")
            r.raise_for_status()
            return r.content

    def tabs_sync(self) -> list[dict]:
        """Synchronous tab fetch for use in non-async contexts (e.g. orchestrator context building)."""
        try:
            req = urllib.request.Request(f"{self._base}/tabs", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = _json.loads(resp.read())
                return data.get("tabs", []) if isinstance(data, dict) else []
        except Exception:
            return []

    # -- Dashboard profile management (v0.7.8+) --

    async def _ensure_profile_running(self) -> bool:
        """In dashboard mode, ensure at least one profile instance is running."""
        try:
            data = await self._get("/health")
            if data.get("mode") != "dashboard":
                return True  # Not dashboard mode, nothing to do

            # Check if any instance is running
            instances = await self._get("/instances")
            if isinstance(instances, list) and len(instances) > 0:
                running = [i for i in instances if i.get("status") == "running"]
                if running:
                    return True

            # No running instance — create and start a profile
            logger.info("PinchTab dashboard: no running instance, creating headless profile")

            # Check for existing profiles
            profiles = await self._get("/profiles")
            profile_id = None
            if isinstance(profiles, list):
                for p in profiles:
                    if p.get("name") == "rdc-default":
                        profile_id = p.get("id")
                        break

            if not profile_id:
                result = await self._post("/profiles", {"name": "rdc-default"})
                profile_id = result.get("id")
                logger.info("PinchTab: created profile %s", profile_id)

            # Start it headless
            result = await self._post(f"/profiles/{profile_id}/start", {"headless": True})
            instance_id = result.get("id")
            logger.info("PinchTab: started instance %s on port %s", instance_id, result.get("port"))

            # Wait for it to be ready
            import asyncio
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    instances = await self._get("/instances")
                    running = [i for i in instances if i.get("status") == "running"]
                    if running:
                        return True
                except Exception:
                    pass

            logger.warning("PinchTab: profile instance failed to become running")
            return False
        except Exception:
            logger.exception("PinchTab: failed to ensure profile running")
            return False

    async def ensure_running(self) -> bool:
        """Auto-start PinchTab via ProcessManager if not running. Returns True if healthy."""
        if await self.health():
            # Healthy — ensure a profile instance is running (dashboard mode)
            return await self._ensure_profile_running()

        cfg = load_pinchtab_config()
        binary = _resolve_binary()
        port = cfg.get("port", 9867)

        try:
            from .processes import get_process_manager
            from .db.models import ProcessType
            pm = get_process_manager()

            process_id = "system-pinchtab"
            cmd = f"{binary} serve --port {port}"

            # Register if not already registered
            existing = pm._processes.get(process_id)
            if not existing:
                from .config import get_rdc_home
                pm.register(
                    project="system",
                    name="pinchtab",
                    command=cmd,
                    cwd=str(get_rdc_home()),
                    process_type=ProcessType.DEV_SERVER,
                    port=port,
                    force_update=True,
                )

            # Start it
            pm.start(process_id)

            # Poll for health up to 15s
            import asyncio
            for _ in range(30):
                await asyncio.sleep(0.5)
                invalidate_health_cache()
                if await self.health():
                    logger.info("PinchTab started on port %d", port)
                    # Ensure a profile instance is running in dashboard mode
                    return await self._ensure_profile_running()

            logger.warning("PinchTab failed to become healthy within 15s")
            return False
        except Exception:
            logger.exception("Failed to auto-start PinchTab")
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_client: Optional[PinchTabClient] = None


def get_pinchtab_client() -> Optional[PinchTabClient]:
    """Get PinchTab client singleton. Returns None if disabled."""
    global _client
    cfg = load_pinchtab_config()
    if not cfg.get("enabled", True):
        return None
    port = cfg.get("port", 9867)
    if _client is None or _client._port != port:
        _client = PinchTabClient(port=port)
    return _client
