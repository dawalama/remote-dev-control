"""browser-use Python API wrapper for agent browser control.

Provides a BrowserUseSession that connects to an existing Chrome instance
via CDP and exposes observe/act/screenshot methods for the agent loop.

Falls back to native CDP methods (via _LiveConnection) when the browser-use
package is not installed.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Try to import browser-use; graceful fallback if not installed
try:
    from browser_use import Browser, BrowserConfig
    BROWSER_USE_AVAILABLE = True
except ImportError:
    BROWSER_USE_AVAILABLE = False
    logger.info("browser-use not installed — using native CDP fallback for browser agent")


class BrowserUseSession:
    """Wraps browser-use to operate on an existing Chrome CDP connection.

    Can also operate in native CDP mode when browser-use is unavailable,
    using the _LiveConnection's existing interactive element methods.
    """

    def __init__(self):
        self._live_conn = None  # _LiveConnection for native CDP

    def update_connection(self, live_conn) -> None:
        """Update the underlying CDP connection (e.g. after reconnect)."""
        self._live_conn = live_conn

    async def observe(self) -> dict[str, Any]:
        """Get current page state: URL, title, and indexed interactive elements.

        Returns dict with keys: url, title, elements (list of {ref, role, name, value?})
        """
        if not self._live_conn:
            return {"error": "No connection", "url": "", "title": "", "elements": []}

        conn = self._live_conn
        if not conn.alive or not conn._session_id:
            return {"error": "Connection lost", "url": "", "title": "", "elements": []}

        sid = conn._session_id

        # Get page info
        try:
            url_r = await conn._cdp("Runtime.evaluate", {"expression": "document.location.href"}, sid)
            title_r = await conn._cdp("Runtime.evaluate", {"expression": "document.title"}, sid)
            url = url_r.get("result", {}).get("value", "")
            title = title_r.get("result", {}).get("value", "")

            # Normalize Docker URLs (legacy compat)
            if "host.docker.internal" in url:
                url = url.replace("host.docker.internal", "localhost")
        except Exception as e:
            return {"error": str(e), "url": "", "title": "", "elements": []}

        # Get interactive elements
        try:
            elements = await conn.get_interactive_elements()
        except Exception as e:
            logger.warning(f"Failed to get interactive elements: {e}")
            elements = []

        return {
            "url": url,
            "title": title,
            "elements": elements,
        }

    async def act(self, action: str, **params) -> dict[str, Any]:
        """Execute a browser action.

        Supported actions:
        - click: Click element by ref. Params: ref
        - type: Type text into element. Params: ref, value, submit=True
        - navigate: Go to URL. Params: url
        - scroll: Scroll page. Params: direction ("up"/"down"), amount (pixels)
        - back: Go back in history. No params.

        Returns {"ok": True} or {"error": "message"}
        """
        if not self._live_conn:
            return {"error": "No connection"}

        conn = self._live_conn

        if action == "click":
            ref = params.get("ref", "")
            return await conn.click_element(ref)

        elif action == "type":
            ref = params.get("ref", "")
            value = params.get("value", "")
            submit = params.get("submit", True)
            return await conn.fill_element(ref, value, submit=submit)

        elif action == "navigate":
            url = params.get("url", "")
            if url and not url.startswith(("http://", "https://", "about:", "data:")):
                url = "https://" + url
            return await conn.navigate(url)

        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = params.get("amount", 400)
            sid = conn._session_id
            if not sid:
                return {"error": "No session"}
            delta_y = -amount if direction == "up" else amount
            try:
                await conn._cdp("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": 640, "y": 450,
                    "deltaX": 0, "deltaY": delta_y,
                }, sid)
                return {"ok": True}
            except Exception as e:
                return {"error": str(e)}

        elif action == "back":
            try:
                await conn.go_back()
                return {"ok": True}
            except Exception as e:
                return {"error": str(e)}

        else:
            return {"error": f"Unknown action: {action}"}

    async def screenshot(self) -> Optional[bytes]:
        """Capture a screenshot of the current page."""
        if not self._live_conn:
            return None

        conn = self._live_conn
        if not conn.alive or not conn._session_id:
            return None

        try:
            ss_result = await conn._cdp("Page.captureScreenshot", {
                "format": "png",
            }, conn._session_id)
            data = ss_result.get("data", "")
            return base64.b64decode(data) if data else None
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None


# Global session cache
_sessions: dict[str, BrowserUseSession] = {}


async def get_or_create_session(
    session_id: str,
    cdp_port: int,
    live_conn=None,
) -> BrowserUseSession:
    """Get or create a BrowserUseSession for the given browser session.

    Always updates the live_conn if provided, so reconnects are picked up.
    """
    if session_id in _sessions:
        bus = _sessions[session_id]
        if live_conn:
            bus.update_connection(live_conn)
        return bus

    bus = BrowserUseSession()
    if live_conn:
        bus.update_connection(live_conn)
    _sessions[session_id] = bus
    return bus


def remove_session(session_id: str) -> None:
    """Remove a cached BrowserUseSession."""
    _sessions.pop(session_id, None)
