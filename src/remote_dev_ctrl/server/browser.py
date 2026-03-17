"""Browser session management via browserless containers + CDP.

Architecture:
- A browserless Docker container runs headless Chromium.
- We hold a persistent browser-level CDP WebSocket connection.
- On session create, we create a page target and navigate to the app URL.
- The user views/interacts with the page via a screencast viewer (canvas +
  CDP input forwarding) in an iframe. The viewer connects at the page level.
- For context capture, we briefly attach to the page target via the
  browser-level WS, take a screenshot + a11y tree, then detach.
- Both user and agent see the SAME page state. User interactions (typing,
  clicking, navigating) happen in the headless browser and are captured
  exactly as-is.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import websockets

from .config import get_rdc_home
from .db.connection import get_db
from .db.models import BrowserSession, BrowserStatus, ContextSnapshot

logger = logging.getLogger(__name__)

BROWSERLESS_IMAGE = "ghcr.io/browserless/chromium"
CONTEXTS_DIR = get_rdc_home() / "contexts"
CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)


class _LiveConnection:
    """Persistent browser-level CDP connection.

    We connect to the browser (not a page) via WebSocket.  We create a page
    target and keep a *persistent* CDP session attached to it.  The screencast
    viewer connects separately (page-level WS) for live interaction.

    For context captures we reuse the persistent session — no new connections
    or attach/detach cycles — so the page is never disrupted.

    CDP messages are handled by a background reader task:
    - Responses (have `id`) are dispatched to pending Futures.
    - Events (no `id`) are dispatched to registered event callbacks.
    """

    def __init__(self):
        self._ws = None
        self._msg_id = 0
        self._send_lock = asyncio.Lock()
        self.target_id: Optional[str] = None
        self.container_port: int = 0
        # Persistent CDP session attached to the page target.
        self._session_id: Optional[str] = None
        # Last device emulation set by the screencast viewer (via CDP proxy)
        self.device_override: Optional[dict] = None
        self.ua_override: Optional[str] = None
        # CDP response routing
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        # Event callbacks: method_name -> list[callback]
        self._event_callbacks: dict[str, list] = {}

    def on_event(self, method: str, callback):
        """Register a callback for a CDP event method (e.g. Runtime.bindingCalled)."""
        self._event_callbacks.setdefault(method, []).append(callback)

    def remove_event(self, method: str, callback):
        """Remove a previously registered callback."""
        cbs = self._event_callbacks.get(method, [])
        if callback in cbs:
            cbs.remove(callback)

    async def _reader_loop(self):
        """Background task that reads all WS messages and dispatches them."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_id = msg.get("id")
                if msg_id is not None:
                    # Response to a command
                    fut = self._pending.pop(msg_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                else:
                    # Event (no id = CDP event notification)
                    method = msg.get("method", "")
                    if method == "Runtime.bindingCalled":
                        logger.info(f"CDP event: Runtime.bindingCalled (name={msg.get('params', {}).get('name', '?')}), {len(self._event_callbacks.get(method, []))} callbacks registered")
                    callbacks = self._event_callbacks.get(method, [])
                    for cb in callbacks:
                        try:
                            cb(msg)
                        except Exception as e:
                            logger.warning(f"Event callback error for {method}: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.debug("CDP WebSocket closed")
        except Exception as e:
            logger.debug(f"CDP reader loop error: {e}")
        finally:
            # Cancel all pending futures
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("CDP connection closed"))
            self._pending.clear()

    async def connect(self, container_port: int, target_url: str):
        self.container_port = container_port
        ws_url = f"ws://localhost:{container_port}"
        self._ws = await websockets.connect(
            ws_url, max_size=50 * 1024 * 1024,
            ping_interval=30, ping_timeout=20,
        )

        # Start background reader
        self._reader_task = asyncio.create_task(self._reader_loop())

        result = await self._cdp("Target.createTarget", {"url": target_url})
        self.target_id = result.get("targetId")
        if not self.target_id:
            raise RuntimeError(f"Failed to create target: {result}")

        # Attach a persistent session — kept alive for the lifetime of this
        # connection.  Used for initial viewport setup AND all future captures.
        result = await self._cdp("Target.attachToTarget", {
            "targetId": self.target_id, "flatten": True,
        })
        self._session_id = result["sessionId"]

        # Set default desktop viewport for initial page load.
        self.device_override = {
            "width": 1280, "height": 900,
            "deviceScaleFactor": 1, "mobile": False,
        }
        await self._cdp("Emulation.setDeviceMetricsOverride",
                         self.device_override, self._session_id)

        for _ in range(30):
            r = await self._cdp("Runtime.evaluate", {
                "expression": "document.readyState",
            }, self._session_id)
            state = r.get("result", {}).get("value", "")
            if state == "complete":
                break
            await asyncio.sleep(0.5)

        # Enable Runtime for binding support
        await self._cdp("Runtime.enable", {}, self._session_id)

        # Keep emulation set (don't clear) — it will be updated in real-time
        # by sync_emulation() when the screencast viewer switches devices.
        # NOTE: we do NOT detach — the session stays alive.
        logger.info(f"Created page target {self.target_id} -> {target_url}")

    async def setup_rrweb_binding(self):
        """Set up the JS->CDP bridge for rrweb event capture.

        Adds a binding `__rdc_rrweb` that the injected rrweb script calls
        with stringified events. Inlines the rrweb library from the local
        static copy (no CDN dependency). Also uses addScriptToEvaluateOnNewDocument
        to re-inject on SPA navigations.

        The setup order matters:
        1. Enable Page + Runtime domains
        2. Add the __rdc_rrweb binding (persists across navigations)
        3. Inject rrweb library
        4. Start recorder with retry loop (binding may take a tick to appear)
        """
        if not self._session_id:
            return

        sid = self._session_id

        # 1. Enable Page + Runtime domains
        await self._cdp("Page.enable", {}, sid)
        await self._cdp("Runtime.enable", {}, sid)

        # 2. Add JS binding FIRST — it persists across new documents
        await self._cdp("Runtime.addBinding", {"name": "__rdc_rrweb"}, sid)
        logger.info("Runtime.addBinding(__rdc_rrweb) done")

        # 3. Read the local rrweb library
        rrweb_path = Path(__file__).parent / "static" / "rrweb.min.js"
        if not rrweb_path.exists():
            logger.error(f"rrweb.min.js not found at {rrweb_path}")
            return
        rrweb_lib = rrweb_path.read_text()

        # 4. Build recorder script.
        # Uses a dual approach:
        #   a) Try to push events via CDP binding (window.__rdc_rrweb)
        #   b) Always buffer events in window.__rdc_rrweb_buffer for pull-based collection
        # This ensures events are captured even if the CDP binding path fails.
        recorder_setup = """
;(function() {
    if (window.__rdc_rrweb_active) return;
    window.__rdc_rrweb_active = true;
    window.__rdc_rrweb_buffer = window.__rdc_rrweb_buffer || [];

    // rrweb may be available as window.rrweb or as local var rrweb
    // (depends on whether we're in addScriptToEvaluateOnNewDocument or
    // a separate Runtime.evaluate call)
    var _rrweb = (typeof rrweb !== 'undefined') ? rrweb : window.rrweb;
    if (!_rrweb || typeof _rrweb.record !== 'function') {
        window.__rdc_rrweb_info = { error: 'rrweb not loaded', rrweb_type: typeof rrweb, window_rrweb_type: typeof window.rrweb };
        return;
    }

    var bindingAvailable = typeof window.__rdc_rrweb === 'function';

    _rrweb.record({
        emit: function(event) {
            // Always buffer for pull-based collection
            window.__rdc_rrweb_buffer.push(event);
            // Also try push via CDP binding if available
            if (typeof window.__rdc_rrweb === 'function') {
                try { window.__rdc_rrweb(JSON.stringify(event)); } catch(e) {}
            }
        },
        sampling: { mousemove: 50, scroll: 150, input: 'last' }
    });

    window.__rdc_rrweb_info = {
        started: true,
        binding_available: bindingAvailable,
        buffer_len: window.__rdc_rrweb_buffer.length
    };
})();
"""
        # 5. Inject rrweb library via Runtime.evaluate, explicitly assigning
        # the IIFE result to window.rrweb. The original IIFE is:
        #   var rrweb = function(ee){...}({});
        # We rewrite it to:
        #   window.rrweb = function(ee){...}({});
        # This ensures the library is a true global regardless of eval scope.
        rrweb_global = rrweb_lib.replace("var rrweb=", "window.rrweb=", 1)
        r_lib = await self._cdp("Runtime.evaluate", {
            "expression": rrweb_global,
            "returnByValue": False,
        }, sid)
        if r_lib.get("exceptionDetails"):
            logger.error(f"rrweb library injection exception: {r_lib['exceptionDetails']}")
            return
        logger.info("rrweb library injected (%d bytes)", len(rrweb_lib))

        # Verify rrweb.record is available
        r_verify = await self._cdp("Runtime.evaluate", {
            "expression": "JSON.stringify({t: typeof window.rrweb, r: typeof window.rrweb?.record})",
            "returnByValue": True,
        }, sid)
        verify_str = r_verify.get("result", {}).get("value", "N/A")
        logger.info(f"rrweb verify: {verify_str}")

        # 6. Inject recorder setup — also via evaluate since it references
        # window.rrweb which we just set
        r_rec = await self._cdp("Runtime.evaluate", {
            "expression": recorder_setup,
            "returnByValue": False,
        }, sid)
        if r_rec.get("exceptionDetails"):
            logger.error(f"rrweb recorder exception: {r_rec['exceptionDetails']}")
            return
        logger.info("rrweb recorder injected")

        # 7. Register combined script for SPA navigations — addScriptToEvaluateOnNewDocument
        # runs in the page's main context on new documents, so var rrweb works fine there
        full_script = rrweb_lib + recorder_setup
        await self._cdp("Page.addScriptToEvaluateOnNewDocument", {
            "source": full_script,
        }, sid)

        # 8. Wait and verify
        await asyncio.sleep(0.3)
        r_check = await self._cdp("Runtime.evaluate", {
            "expression": "JSON.stringify(window.__rdc_rrweb_info || 'not set')",
            "returnByValue": True,
        }, sid)
        info_str = r_check.get("result", {}).get("value", "N/A")
        logger.info(f"rrweb status: {info_str}")

        r_buf = await self._cdp("Runtime.evaluate", {
            "expression": "(window.__rdc_rrweb_buffer || []).length",
            "returnByValue": True,
        }, sid)
        buf_count = r_buf.get("result", {}).get("value", 0)
        logger.info(f"rrweb buffer has {buf_count} events after setup")

        if buf_count == 0:
            r_diag = await self._cdp("Runtime.evaluate", {
                "expression": "JSON.stringify({rrweb: typeof rrweb, win_rrweb: typeof window.rrweb, record_fn: typeof window.rrweb?.record, active: window.__rdc_rrweb_active, bufLen: (window.__rdc_rrweb_buffer||[]).length, info: window.__rdc_rrweb_info, docReady: document.readyState, bodyChildren: document.body?.children?.length})",
                "returnByValue": True,
            }, sid)
            logger.warning(f"rrweb 0 events! Diagnostic: {r_diag.get('result', {}).get('value', 'N/A')}")

        logger.info("rrweb setup complete (%d bytes lib + %d bytes setup)", len(rrweb_lib), len(recorder_setup))

    async def navigate(self, url: str) -> dict:
        """Navigate the existing page target to a new URL.

        Returns {"ok": True} or {"error": "..."}.
        """
        if not self._session_id:
            return {"error": "No active session"}
        result = await self._cdp("Page.navigate", {"url": url}, self._session_id)
        # Check for navigation error (e.g. DNS failure, net::ERR_*)
        error_text = result.get("errorText", "")
        if error_text:
            return {"error": f"Navigation failed: {error_text}"}
        # Wait briefly for load — don't block too long (screencast stalls)
        for _ in range(10):
            try:
                r = await self._cdp("Runtime.evaluate", {
                    "expression": "document.readyState",
                }, self._session_id)
                state = r.get("result", {}).get("value", "")
                if state in ("complete", "interactive"):
                    break
            except Exception:
                break
            await asyncio.sleep(0.3)
        return {"ok": True}

    async def reload(self):
        """Reload the current page."""
        if not self._session_id:
            raise RuntimeError("No active session")
        await self._cdp("Page.reload", {}, self._session_id)
        for _ in range(30):
            r = await self._cdp("Runtime.evaluate", {
                "expression": "document.readyState",
            }, self._session_id)
            state = r.get("result", {}).get("value", "")
            if state == "complete":
                break
            await asyncio.sleep(0.5)

    async def go_back(self):
        """Navigate back in browser history."""
        if not self._session_id:
            raise RuntimeError("No active session")
        nav = await self._cdp("Page.getNavigationHistory", {}, self._session_id)
        current_index = nav.get("currentIndex", 0)
        entries = nav.get("entries", [])
        if current_index <= 0 or not entries:
            return  # Already at the beginning
        prev_entry = entries[current_index - 1]
        await self._cdp("Page.navigateToHistoryEntry", {
            "entryId": prev_entry["id"],
        }, self._session_id)
        for _ in range(30):
            r = await self._cdp("Runtime.evaluate", {
                "expression": "document.readyState",
            }, self._session_id)
            state = r.get("result", {}).get("value", "")
            if state == "complete":
                break
            await asyncio.sleep(0.5)

    async def sync_emulation(self, device_params: Optional[dict] = None,
                              ua: Optional[str] = None, clear: bool = False):
        """Mirror the viewer's emulation state to our persistent session.

        Called by the CDP proxy whenever the screencast viewer changes device
        emulation, so our capture session stays in sync.
        """
        if not self._session_id:
            return
        try:
            if clear:
                self.device_override = None
                self.ua_override = None
                await self._cdp("Emulation.clearDeviceMetricsOverride", {},
                                self._session_id)
            else:
                if device_params is not None:
                    self.device_override = device_params
                    await self._cdp("Emulation.setDeviceMetricsOverride",
                                    device_params, self._session_id)
                if ua is not None:
                    self.ua_override = ua
                    await self._cdp("Emulation.setUserAgentOverride",
                                    {"userAgent": ua}, self._session_id)
        except Exception as e:
            logger.debug(f"sync_emulation failed (non-fatal): {e}")

    async def _cdp(self, method: str, params: dict = None, session_id: str = None) -> dict:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        async with self._send_lock:
            self._msg_id += 1
            mid = self._msg_id
            self._pending[mid] = fut
            msg: dict = {"id": mid, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            await self._ws.send(json.dumps(msg))

        try:
            resp = await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise RuntimeError(f"CDP timeout: {method}")

        if "error" in resp:
            raise RuntimeError(f"CDP error: {resp['error']}")
        return resp.get("result", {})

    async def capture_all(self) -> tuple[dict, bytes, list[dict]]:
        """Grab page info + screenshot + a11y tree via the persistent session.

        Returns (page_info_dict, screenshot_bytes, a11y_nodes).
        Reuses the session that was attached during connect() — no new
        connections, no attach/detach, no page disruption.
        """
        if not self.target_id or not self._session_id:
            raise RuntimeError("No target page")

        sid = self._session_id

        # Page info
        url_r = await self._cdp("Runtime.evaluate", {"expression": "document.location.href"}, sid)
        title_r = await self._cdp("Runtime.evaluate", {"expression": "document.title"}, sid)
        # Measure the page's current layout dimensions for the screenshot clip
        # and viewport metadata.
        dims_r = await self._cdp("Runtime.evaluate", {
            "expression": (
                "JSON.stringify({"
                "w:Math.max(document.documentElement.scrollWidth,document.documentElement.clientWidth),"
                "h:Math.max(document.documentElement.scrollHeight,document.documentElement.clientHeight),"
                "dpr:window.devicePixelRatio||1})"
            ),
        }, sid)
        try:
            dims = json.loads(dims_r.get("result", {}).get("value", "{}"))
            fw = dims.get("w", 1280)
            fh = dims.get("h", 900)
            dpr = dims.get("dpr", 1)
        except (json.JSONDecodeError, TypeError):
            fw, fh, dpr = 1280, 900, 1

        page_info = {
            "url": url_r.get("result", {}).get("value", ""),
            "title": title_r.get("result", {}).get("value", ""),
            "viewport": {"width": fw, "height": fh, "dpr": dpr},
        }

        # Stop any active screencast on our session before capturing — a
        # concurrent screencast (from the viewer's page-level WS) can cause
        # captureScreenshot with captureBeyondViewport/clip to return blank.
        try:
            await self._cdp("Page.stopScreencast", {}, sid)
        except Exception:
            pass

        # Simple viewport capture — avoids captureBeyondViewport + clip which
        # conflict with screencast rendering pipeline.
        ss_result = await self._cdp("Page.captureScreenshot", {
            "format": "png",
        }, sid)
        screenshot = base64.b64decode(ss_result.get("data", ""))

        # Accessibility tree
        a11y_result = await self._cdp("Accessibility.getFullAXTree", {}, sid)
        a11y_nodes = a11y_result.get("nodes", [])

        return page_info, screenshot, a11y_nodes

    # -- Interactive element methods (for browser agent) --

    _INTERACTIVE_ROLES = frozenset([
        "link", "button", "textbox", "combobox", "searchbox",
        "input", "textarea", "menuitem", "menuitemcheckbox",
        "menuitemradio", "tab", "checkbox", "radio", "switch",
        "option", "treeitem", "listitem",
    ])

    async def get_interactive_elements(self) -> list[dict]:
        """Get interactive elements from the a11y tree with sequential refs.

        Builds ``_ref_map`` mapping ref → backendDOMNodeId for CDP interaction.
        Returns a list of ``{"ref": "e0", "role": "button", "name": "Login"}``.
        """
        if not self._session_id:
            raise RuntimeError("No active session")

        sid = self._session_id
        a11y_result = await self._cdp("Accessibility.getFullAXTree", {}, sid)
        nodes = a11y_result.get("nodes", [])

        self._ref_map: dict[str, int] = {}
        elements: list[dict] = []
        idx = 0

        for node in nodes:
            role = node.get("role", {}).get("value", "")
            if role not in self._INTERACTIVE_ROLES:
                continue
            backend_id = node.get("backendDOMNodeId")
            if not backend_id:
                continue

            name_prop = node.get("name", {})
            name = name_prop.get("value", "") if isinstance(name_prop, dict) else str(name_prop)
            value_prop = node.get("value", {})
            value = value_prop.get("value", "") if isinstance(value_prop, dict) else ""

            ref = f"e{idx}"
            self._ref_map[ref] = backend_id
            entry: dict = {"ref": ref, "role": role, "name": name}
            if value:
                entry["value"] = value
            elements.append(entry)
            idx += 1

        return elements

    async def _resolve_ref(self, ref: str) -> str:
        """Resolve a ref string to a CDP RemoteObject objectId."""
        ref_map = getattr(self, "_ref_map", None)
        if not ref_map or ref not in ref_map:
            raise ValueError(f"Unknown ref: {ref}")
        backend_id = ref_map[ref]
        result = await self._cdp("DOM.resolveNode", {
            "backendNodeId": backend_id,
        }, self._session_id)
        obj = result.get("object", {})
        object_id = obj.get("objectId")
        if not object_id:
            raise RuntimeError(f"Could not resolve DOM node for {ref}")
        return object_id

    async def _get_element_center(self, ref: str, object_id: str) -> tuple[float, float]:
        """Get element center coordinates, scrolling only if off-screen."""
        sid = self._session_id
        # Use JS to check visibility and get coords in one call — avoids
        # the jarring scrollIntoView that disrupts the screencast.
        r = await self._cdp("Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": """function() {
                var r = this.getBoundingClientRect();
                var vw = window.innerWidth, vh = window.innerHeight;
                var visible = r.top < vh && r.bottom > 0 && r.left < vw && r.right > 0;
                if (!visible) {
                    this.scrollIntoView({block:'center', behavior:'instant'});
                    r = this.getBoundingClientRect();
                }
                return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
            }""",
            "returnByValue": True,
        }, sid)
        coords = json.loads(r.get("result", {}).get("value", "{}"))
        return coords.get("x", 0), coords.get("y", 0)

    async def click_element(self, ref: str) -> dict:
        """Click an element via real CDP mouse events at its coordinates."""
        try:
            object_id = await self._resolve_ref(ref)
            sid = self._session_id
            cx, cy = await self._get_element_center(ref, object_id)

            for etype in ("mousePressed", "mouseReleased"):
                await self._cdp("Input.dispatchMouseEvent", {
                    "type": etype, "x": cx, "y": cy,
                    "button": "left", "clickCount": 1,
                }, sid)

            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

    async def fill_element(self, ref: str, value: str, submit: bool = True) -> dict:
        """Fill an input via real CDP keyboard events (React/framework-compatible)."""
        try:
            object_id = await self._resolve_ref(ref)
            sid = self._session_id

            # Click to focus (real mouse event — triggers React focus handlers)
            cx, cy = await self._get_element_center(ref, object_id)
            for etype in ("mousePressed", "mouseReleased"):
                await self._cdp("Input.dispatchMouseEvent", {
                    "type": etype, "x": cx, "y": cy,
                    "button": "left", "clickCount": 1,
                }, sid)
            await asyncio.sleep(0.05)

            # Select all (Ctrl+A — Docker Chrome runs Linux), then replace
            await self._cdp("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "code": "KeyA",
                "windowsVirtualKeyCode": 65, "modifiers": 2,
            }, sid)
            await self._cdp("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "a", "code": "KeyA",
                "windowsVirtualKeyCode": 65, "modifiers": 2,
            }, sid)
            await asyncio.sleep(0.05)

            # Type the text — fires native input events that frameworks detect
            await self._cdp("Input.insertText", {"text": value}, sid)

            if submit:
                await asyncio.sleep(0.2)
                await self._cdp("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                }, sid)
                await self._cdp("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                }, sid)

            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

    async def capture(self) -> tuple[bytes, list[dict]]:
        """Capture screenshot + a11y tree."""
        _, screenshot, a11y_nodes = await self.capture_all()
        return screenshot, a11y_nodes

    async def get_page_info(self) -> dict:
        """Get URL and title."""
        if not self.target_id:
            return {}
        page_info, _, _ = await self.capture_all()
        return page_info

    def get_viewer_url(self) -> str:
        if self.target_id and self.container_port:
            ws_param = f"localhost:{self.container_port}/devtools/page/{self.target_id}"
            return f"/browser/viewer?ws={ws_param}"
        return ""

    async def close(self):
        if self._session_id:
            try:
                await self._cdp("Target.detachFromTarget", {"sessionId": self._session_id})
            except Exception:
                pass
            self._session_id = None
        if self.target_id:
            try:
                await self._cdp("Target.closeTarget", {"targetId": self.target_id})
            except Exception:
                pass
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self.target_id = None
        self._event_callbacks.clear()

    @property
    def alive(self) -> bool:
        if self._ws is None:
            return False
        try:
            return self._ws.state.name == "OPEN"
        except Exception:
            return False


class BrowserManager:
    """Manages browserless containers and Playwright connections."""

    def __init__(self):
        self._connections: dict[str, _LiveConnection] = {}

    # -- Session lifecycle --

    async def create_session(
        self,
        process_id: str,
        target_url: str,
    ) -> BrowserSession:
        db = get_db("rdc")

        # Clean up stale session for same process
        existing = self._load_session_by_process(process_id)
        if existing:
            if existing.status == BrowserStatus.RUNNING:
                if self._is_container_running(existing.container_id):
                    if existing.id not in self._connections:
                        await self._connect(existing)
                    return existing
            await self.stop_session(existing.id)

        port = self._find_available_port(9500)
        session_id = f"browser-{process_id}"
        container_name = f"rdc-browser-{process_id}"

        # Remove leftover container
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{port}:3000",
            "--add-host=host.docker.internal:host-gateway",
            BROWSERLESS_IMAGE,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"Docker failed: {result.stderr.strip()}")
            container_id = result.stdout.strip()
        except Exception as e:
            session = BrowserSession(
                id=session_id, process_id=process_id, target_url=target_url,
                container_port=port, status=BrowserStatus.FAILED, error=str(e),
            )
            self._save_session(session)
            return session

        # Rewrite localhost for docker
        docker_url = target_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")

        session = BrowserSession(
            id=session_id, process_id=process_id, target_url=target_url,
            container_id=container_id, container_port=port,
            status=BrowserStatus.STARTING,
        )
        self._save_session(session)

        # Wait for browserless, then create page target via CDP
        try:
            await self._wait_for_ready(port)
            conn = _LiveConnection()
            await conn.connect(port, docker_url)
            self._connections[session_id] = conn
            session.status = BrowserStatus.RUNNING
        except Exception as e:
            logger.error(f"Failed to connect to browserless: {e}")
            session.status = BrowserStatus.FAILED
            session.error = str(e)

        self._save_session(session)
        return session

    async def create_standalone_session(
        self,
        target_url: str,
        project_id: str = "",
    ) -> BrowserSession:
        """Create a browser session not tied to any process."""
        session_id = f"browser-{secrets.token_hex(4)}"
        container_name = f"rdc-browser-{session_id}"
        port = self._find_available_port(9500)

        # Remove leftover container
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{port}:3000",
            "--add-host=host.docker.internal:host-gateway",
            BROWSERLESS_IMAGE,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"Docker failed: {result.stderr.strip()}")
            container_id = result.stdout.strip()
        except Exception as e:
            session = BrowserSession(
                id=session_id, process_id=None, target_url=target_url,
                project_id=project_id or None,
                container_port=port, status=BrowserStatus.FAILED, error=str(e),
            )
            self._save_session(session)
            return session

        docker_url = target_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")

        session = BrowserSession(
            id=session_id, process_id=None, target_url=target_url,
            project_id=project_id or None,
            container_id=container_id, container_port=port,
            status=BrowserStatus.STARTING,
        )
        self._save_session(session)

        try:
            await self._wait_for_ready(port)
            conn = _LiveConnection()
            await conn.connect(port, docker_url)
            self._connections[session_id] = conn
            session.status = BrowserStatus.RUNNING
        except Exception as e:
            logger.error(f"Failed to connect to browserless: {e}")
            session.status = BrowserStatus.FAILED
            session.error = str(e)

        self._save_session(session)
        return session

    async def reload_session(self, session_id: str) -> bool:
        """Reload the current page in a session."""
        conn = await self._ensure_connection(session_id)
        if not conn:
            return False
        await conn.reload()
        return True

    async def go_back_session(self, session_id: str) -> bool:
        """Go back in browser history for a session."""
        conn = await self._ensure_connection(session_id)
        if not conn:
            return False
        await conn.go_back()
        return True

    async def navigate_session(self, session_id: str, url: str) -> bool:
        """Navigate an existing session to a new URL."""
        conn = await self._ensure_connection(session_id)
        if not conn:
            return False
        docker_url = url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        result = await conn.navigate(docker_url)
        if result.get("error"):
            return False
        # Update target_url in DB
        session = self._load_session(session_id)
        if session:
            session.target_url = url
            self._save_session(session)
        return True

    async def stop_session(self, session_id: str) -> bool:
        session = self._load_session(session_id)
        if not session:
            return False

        # Auto-stop any active recording before killing the connection
        try:
            await self.stop_recording(session_id)
        except Exception as e:
            logger.warning(f"Error stopping recording for session {session_id}: {e}")

        conn = self._connections.pop(session_id, None)
        if conn:
            await conn.close()

        if session.container_id:
            # Use process_id for process-bound sessions, session_id for standalone
            container_name = f"rdc-browser-{session.process_id}" if session.process_id else f"rdc-browser-{session_id}"
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)

        session.status = BrowserStatus.STOPPED
        session.stopped_at = datetime.now()
        session.container_id = None
        self._save_session(session)
        return True

    def list_sessions(self) -> list[BrowserSession]:
        db = get_db("rdc")
        rows = db.execute("SELECT * FROM browser_sessions ORDER BY created_at DESC").fetchall()
        sessions = []
        for row in rows:
            s = self._row_to_session(row)
            if s.status == BrowserStatus.RUNNING and s.container_id:
                if not self._is_container_running(s.container_id):
                    s.status = BrowserStatus.STOPPED
                    s.container_id = None
                    self._save_session(s)
            sessions.append(s)
        return sessions

    async def ensure_connections(self):
        """Reconnect to any running sessions that lack an in-memory connection."""
        for s in self.list_sessions():
            if s.status == BrowserStatus.RUNNING and s.id not in self._connections:
                try:
                    await self._connect(s)
                    logger.info(f"Reconnected to session {s.id}")
                except Exception as e:
                    logger.error(f"Failed to reconnect {s.id}: {e}")

    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        session = self._load_session(session_id)
        if session and session.status == BrowserStatus.RUNNING and session.container_id:
            if not self._is_container_running(session.container_id):
                session.status = BrowserStatus.STOPPED
                session.container_id = None
                self._save_session(session)
        return session

    def get_by_process(self, process_id: str) -> Optional[BrowserSession]:
        return self._load_session_by_process(process_id)

    # -- Recording --

    async def start_recording(self, session_id: str, project_id: str = ""):
        """Start rrweb recording for a browser session."""
        from .recording import get_recording_manager

        conn = await self._ensure_connection(session_id)
        if not conn:
            logger.error(f"start_recording: no connection for {session_id}")
            return None

        rm = get_recording_manager()
        recording = rm.start_recording(session_id, project_id)

        rec_id = recording.id  # capture in closure
        conn._rrweb_recording_id = rec_id  # type: ignore[attr-defined]

        # NOTE: We only use poll-based collection (not CDP binding callbacks)
        # to avoid duplicate events. The binding still exists for diagnostics
        # but we don't route its events to the RecordingManager.

        # Inject rrweb + start recording in page
        await conn.setup_rrweb_binding()
        logger.info(f"Recording {rec_id}: rrweb injected, starting poll loop")

        # Start a background poll task to drain the JS buffer periodically.
        # This is the reliable fallback — even if CDP binding events don't
        # arrive, we still collect events via Runtime.evaluate.
        async def _poll_buffer():
            """Periodically drain window.__rdc_rrweb_buffer via CDP evaluate."""
            drain_js = """(function() {
    var buf = window.__rdc_rrweb_buffer || [];
    var events = buf.splice(0, buf.length);
    return JSON.stringify(events);
})()"""
            poll_count = 0
            total_events = 0
            while True:
                await asyncio.sleep(2)
                poll_count += 1
                try:
                    if not conn.alive or not conn._session_id:
                        logger.info(f"Recording {rec_id}: poll loop stopping (connection lost)")
                        break
                    r = await conn._cdp("Runtime.evaluate", {
                        "expression": drain_js,
                        "returnByValue": True,
                    }, conn._session_id)
                    # Check for JS exceptions
                    if r.get("exceptionDetails"):
                        logger.warning(f"Recording {rec_id}: poll drain exception: {r['exceptionDetails']}")
                        continue
                    raw = r.get("result", {}).get("value", "[]")
                    events = json.loads(raw) if raw else []
                    if events:
                        for evt in events:
                            rm.on_event(rec_id, json.dumps(evt))
                        # Force flush to disk immediately so events aren't lost
                        rm._flush(rec_id)
                        total_events += len(events)
                        logger.info(f"Recording {rec_id}: polled {len(events)} events (total: {total_events})")
                    elif poll_count <= 3:
                        # Log first few empty polls to help diagnose
                        logger.info(f"Recording {rec_id}: poll #{poll_count} returned 0 events (raw type={r.get('result', {}).get('type', '?')})")
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Recording {rec_id}: poll error: {e}")

        poll_task = asyncio.create_task(_poll_buffer())
        conn._rrweb_poll_task = poll_task  # type: ignore[attr-defined]

        return recording

    async def stop_recording(self, session_id: str):
        """Stop rrweb recording for a browser session."""
        from .recording import get_recording_manager

        conn = self._connections.get(session_id)
        rm = get_recording_manager()

        # Find active recording
        recording = rm.get_active_recording_for_session(session_id)
        if not recording:
            return None

        # Cancel poll task
        if conn:
            poll_task = getattr(conn, "_rrweb_poll_task", None)
            if poll_task and not poll_task.done():
                poll_task.cancel()
            conn._rrweb_poll_task = None  # type: ignore[attr-defined]

            # Final drain of the JS buffer before stopping
            if conn.alive and conn._session_id:
                try:
                    r = await conn._cdp("Runtime.evaluate", {
                        "expression": """(function() {
                            var buf = window.__rdc_rrweb_buffer || [];
                            var events = buf.splice(0, buf.length);
                            window.__rdc_rrweb_active = false;
                            return JSON.stringify(events);
                        })()""",
                        "returnByValue": True,
                    }, conn._session_id)
                    if r.get("exceptionDetails"):
                        logger.warning(f"Recording {recording.id}: final drain exception: {r['exceptionDetails']}")
                    else:
                        raw = r.get("result", {}).get("value", "[]")
                        events = json.loads(raw) if raw else []
                        for evt in events:
                            rm.on_event(recording.id, json.dumps(evt))
                        logger.info(f"Recording {recording.id}: final drain got {len(events)} events")
                except Exception as e:
                    logger.warning(f"Recording {recording.id}: final drain failed: {e}")

            # Remove binding callback
            cb = getattr(conn, "_rrweb_callback", None)
            if cb:
                conn.remove_event("Runtime.bindingCalled", cb)
                conn._rrweb_callback = None  # type: ignore[attr-defined]
                conn._rrweb_recording_id = None  # type: ignore[attr-defined]

        return rm.stop_recording(recording.id)

    # -- Context capture --

    async def _ensure_connection(self, session_id: str) -> Optional[_LiveConnection]:
        """Get a live connection, reconnecting if stale."""
        conn = self._connections.get(session_id)
        if conn and conn.alive:
            return conn

        if conn:
            logger.info(f"Stale CDP connection for {session_id} (ws={conn._ws.state.name if conn._ws else 'None'}), reconnecting...")
            await conn.close()
            self._connections.pop(session_id, None)

        session = self._load_session(session_id)
        if not session:
            logger.warning(f"_ensure_connection: session {session_id} not found in DB")
            return None
        if session.status != BrowserStatus.RUNNING:
            logger.warning(f"_ensure_connection: session {session_id} status is {session.status.value}, not running")
            return None

        # Check container — try by name if container_id inspect fails
        container_running = False
        if session.container_id:
            container_running = self._is_container_running(session.container_id)
        if not container_running:
            # Try by container name as fallback (docker inspect by name)
            container_name = f"rdc-browser-{session.process_id}" if session.process_id else f"rdc-browser-{session_id}"
            container_running = self._is_container_running(container_name)
            if container_running:
                logger.info(f"Container found by name {container_name} (id lookup failed)")

        if not container_running:
            logger.warning(f"_ensure_connection: container not running for {session_id} (id={session.container_id})")
            # Mark session as stopped since container is gone
            session.status = BrowserStatus.STOPPED
            session.container_id = None
            self._save_session(session)
            return None

        try:
            await self._connect(session)
            new_conn = self._connections.get(session_id)
            if new_conn:
                logger.info(f"Reconnected to {session_id} successfully")
            return new_conn
        except Exception as e:
            logger.error(f"Reconnect failed for {session_id}: {e}")
            return None

    def get_viewer_url(self, session_id: str) -> str:
        conn = self._connections.get(session_id)
        return conn.get_viewer_url() if conn else ""

    async def set_device_override(self, target_id: str, params: dict) -> None:
        """Mirror device emulation from the screencast viewer to our capture session.

        Called by the CDP proxy when it sees Emulation.setDeviceMetricsOverride.
        """
        for conn in self._connections.values():
            if conn.target_id == target_id:
                await conn.sync_emulation(device_params=params)
                logger.debug(f"Device override for {target_id}: {params.get('width')}x{params.get('height')}")
                return

    async def set_ua_override(self, target_id: str, user_agent: str) -> None:
        """Mirror user-agent override from the screencast viewer."""
        for conn in self._connections.values():
            if conn.target_id == target_id:
                await conn.sync_emulation(ua=user_agent)
                return

    async def clear_device_override(self, target_id: str) -> None:
        """Clear device emulation (viewer switched back to default)."""
        for conn in self._connections.values():
            if conn.target_id == target_id:
                await conn.sync_emulation(clear=True)
                return

    async def capture_context(
        self, session_id: str, project_id: str = "", description: str = "",
    ) -> Optional[ContextSnapshot]:
        conn = await self._ensure_connection(session_id)
        if not conn:
            logger.warning(f"No live connection for {session_id}")
            return None

        ctx_id = secrets.token_hex(4)

        try:
            page_info, screenshot, a11y_nodes = await conn.capture_all()
            a11y_tree = self._simplify_a11y(a11y_nodes)

            url = page_info.get("url", "")
            title = page_info.get("title", "")
            # Rewrite Docker internal URL back to localhost for display
            if "host.docker.internal" in url:
                url = url.replace("host.docker.internal", "localhost")

            meta = {
                "url": url,
                "title": title,
                "viewport": page_info.get("viewport", {}),
                "timestamp": datetime.now().isoformat(),
                "description": description,
                "a11y_node_count": len(a11y_nodes),
            }
        except Exception as e:
            logger.error(f"Context capture failed: {e}")
            return None

        if not project_id:
            session = self._load_session(session_id)
            if session and session.project_id:
                project_id = session.project_id

        # Resolve project name for display
        project_name = ""
        if project_id:
            from .db.repositories import _resolve_project_name
            project_name = _resolve_project_name(project_id)

        ss_path = CONTEXTS_DIR / f"{ctx_id}.png"
        a11y_path = CONTEXTS_DIR / f"{ctx_id}.a11y.json"
        meta_path = CONTEXTS_DIR / f"{ctx_id}.meta.json"

        ss_path.write_bytes(screenshot)
        a11y_path.write_text(json.dumps(a11y_tree, indent=2))
        meta_path.write_text(json.dumps(meta, indent=2))

        snapshot = ContextSnapshot(
            id=ctx_id, project_id=project_id, project=project_name,
            session_id=session_id,
            url=url, title=title,
            screenshot_path=str(ss_path), a11y_path=str(a11y_path),
            meta_path=str(meta_path), description=description,
        )
        if project_id:
            self._save_context(snapshot)
        else:
            logger.warning(f"Context {ctx_id} captured but not persisted to DB (no project_id)")
        return snapshot

    async def capture_url(self, url: str, port: int = 0, project_id: str = "") -> Optional[ContextSnapshot]:
        """Capture context at a URL, reusing or creating a headless session.

        Used by the reverse proxy capture button — finds a running session
        that targets the same port, navigates it to the exact URL, and captures.
        """
        target_url = url or f"http://localhost:{port}"

        # Try to find an existing session for this port
        for sid, conn in self._connections.items():
            session = self._load_session(sid)
            if session and session.status == BrowserStatus.RUNNING:
                if port and str(port) in (session.target_url or ""):
                    # Navigate to the exact URL and capture
                    docker_url = target_url.replace("localhost", "host.docker.internal")
                    try:
                        await conn.navigate(docker_url)
                    except Exception:
                        pass
                    return await self.capture_context(sid, project_id)

        # No existing session — create a standalone one
        try:
            session = await self.create_standalone_session(target_url, project_id)
            return await self.capture_context(session.id, project_id)
        except Exception as e:
            logger.error(f"capture_url failed for {target_url}: {e}")
            return None

    def list_contexts(self, project_id: str = "", limit: int = 50) -> list[ContextSnapshot]:
        db = get_db("rdc")
        if project_id:
            # Include contexts for this project AND unassigned contexts (empty project_id)
            rows = db.execute(
                "SELECT c.*, p.name as project_name FROM contexts c LEFT JOIN projects p ON c.project_id = p.id WHERE c.project_id = ? OR c.project_id = '' OR c.project_id IS NULL ORDER BY c.timestamp DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT c.*, p.name as project_name FROM contexts c LEFT JOIN projects p ON c.project_id = p.id ORDER BY c.timestamp DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self._row_to_context(r) for r in rows]

    def get_context(self, context_id: str) -> Optional[ContextSnapshot]:
        db = get_db("rdc")
        row = db.execute(
            "SELECT c.*, p.name as project_name FROM contexts c LEFT JOIN projects p ON c.project_id = p.id WHERE c.id = ?",
            (context_id,),
        ).fetchone()
        return self._row_to_context(row) if row else None

    def delete_context(self, context_id: str) -> bool:
        ctx = self.get_context(context_id)
        if not ctx:
            return False
        for path_str in [ctx.screenshot_path, ctx.a11y_path, ctx.meta_path]:
            if path_str:
                p = Path(path_str)
                if p.exists():
                    p.unlink()
        db = get_db("rdc")
        db.execute("DELETE FROM contexts WHERE id = ?", (context_id,))
        db.commit()
        return True

    async def stop_all(self):
        for sid in list(self._connections.keys()):
            await self.stop_session(sid)

    # -- Internals --

    def _simplify_a11y(self, nodes: list[dict]) -> list[dict]:
        """Simplify the raw CDP AXTree into an agent-friendly format."""
        simplified = []
        for node in nodes:
            role = node.get("role", {}).get("value", "")
            name_prop = node.get("name", {})
            name = name_prop.get("value", "") if isinstance(name_prop, dict) else str(name_prop)
            if role in ("none", "generic", "InlineTextBox") and not name:
                continue
            entry = {"role": role}
            if name:
                entry["name"] = name
            desc = node.get("description", {})
            if isinstance(desc, dict) and desc.get("value"):
                entry["description"] = desc["value"]
            value = node.get("value", {})
            if isinstance(value, dict) and value.get("value"):
                entry["value"] = value["value"]
            props = {}
            for prop in node.get("properties", []):
                pname = prop.get("name", "")
                pval = prop.get("value", {}).get("value")
                if pname in ("focused", "expanded", "checked", "disabled", "required") and pval:
                    props[pname] = pval
            if props:
                entry["properties"] = props
            simplified.append(entry)
        return simplified

    async def _wait_for_ready(self, port: int, timeout: float = 15.0):
        import httpx
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"http://localhost:{port}/json/version", timeout=2)
                    if resp.status_code == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(0.5)
        raise TimeoutError(f"Browserless on port {port} not ready after {timeout}s")

    def _is_container_running(self, container_id: str | None) -> bool:
        if not container_id:
            return False
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0 and "true" in result.stdout.lower()
        except Exception:
            return False

    def _find_available_port(self, start: int = 9500) -> int:
        import socket
        for port in range(start, start + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
                    return port
            except OSError:
                continue
        raise RuntimeError("No available ports")

    async def _connect(self, session: BrowserSession):
        """Reconnect to a running browserless container.

        Tries to reattach to an existing page target first (preserving user
        state). Only creates a new target as a fallback.
        """
        docker_url = session.target_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        conn = _LiveConnection()

        # Connect to the browser-level WS
        conn.container_port = session.container_port
        ws_url = f"ws://localhost:{session.container_port}"
        conn._ws = await websockets.connect(
            ws_url, max_size=50 * 1024 * 1024,
            ping_interval=30, ping_timeout=20,
        )
        conn._reader_task = asyncio.create_task(conn._reader_loop())

        # Try to find existing page targets to reattach to
        try:
            result = await conn._cdp("Target.getTargets")
            targets = result.get("targetInfos", [])
            page_targets = [t for t in targets if t.get("type") == "page"]
            if page_targets:
                # Reattach to the first existing page — preserves user state
                conn.target_id = page_targets[0]["targetId"]
                attach_result = await conn._cdp("Target.attachToTarget", {
                    "targetId": conn.target_id, "flatten": True,
                })
                conn._session_id = attach_result["sessionId"]
                await conn._cdp("Runtime.enable", {}, conn._session_id)
                logger.info(f"Reattached to existing target {conn.target_id}")
                self._connections[session.id] = conn
                return
        except Exception as e:
            logger.warning(f"Could not reattach to existing target: {e}")

        # Fallback: create a new target (will reset the page)
        await conn.close()
        conn2 = _LiveConnection()
        await conn2.connect(session.container_port, docker_url)
        self._connections[session.id] = conn2

    # -- DB helpers --

    def _save_session(self, session: BrowserSession):
        db = get_db("rdc")
        db.execute("""
            INSERT OR REPLACE INTO browser_sessions
            (id, process_id, project_id, target_url, container_id, container_port, status, created_at, stopped_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.id, session.process_id, session.project_id, session.target_url,
            session.container_id, session.container_port, session.status.value,
            session.created_at.isoformat(),
            session.stopped_at.isoformat() if session.stopped_at else None,
            session.error,
        ))
        db.commit()

    def _load_session(self, session_id: str) -> Optional[BrowserSession]:
        db = get_db("rdc")
        row = db.execute("SELECT * FROM browser_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def _load_session_by_process(self, process_id: str) -> Optional[BrowserSession]:
        db = get_db("rdc")
        row = db.execute("SELECT * FROM browser_sessions WHERE process_id = ?", (process_id,)).fetchone()
        return self._row_to_session(row) if row else None

    def _row_to_session(self, row) -> BrowserSession:
        row_dict = dict(row)
        return BrowserSession(
            id=row_dict["id"], process_id=row_dict.get("process_id"),
            project_id=row_dict.get("project_id"),
            target_url=row_dict["target_url"],
            container_id=row_dict["container_id"], container_port=row_dict["container_port"] or 0,
            status=BrowserStatus(row_dict["status"]),
            created_at=datetime.fromisoformat(row_dict["created_at"]) if row_dict["created_at"] else datetime.now(),
            stopped_at=datetime.fromisoformat(row_dict["stopped_at"]) if row_dict.get("stopped_at") else None,
            error=row_dict["error"],
        )

    def _save_context(self, ctx: ContextSnapshot):
        db = get_db("rdc")
        db.execute("""
            INSERT INTO contexts
            (id, project_id, session_id, url, title, timestamp, screenshot_path, a11y_path, meta_path, description, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ctx.id, ctx.project_id, ctx.session_id, ctx.url, ctx.title,
            ctx.timestamp.isoformat(), ctx.screenshot_path, ctx.a11y_path,
            ctx.meta_path, ctx.description, ctx.source,
        ))
        db.commit()

    def _row_to_context(self, row) -> ContextSnapshot:
        row_dict = dict(row)
        return ContextSnapshot(
            id=row_dict["id"],
            project_id=row_dict["project_id"] or "",
            project=row_dict.get("project_name") or "",
            session_id=row_dict["session_id"],
            url=row_dict["url"], title=row_dict["title"],
            timestamp=datetime.fromisoformat(row_dict["timestamp"]) if row_dict["timestamp"] else datetime.now(),
            screenshot_path=row_dict["screenshot_path"], a11y_path=row_dict["a11y_path"],
            meta_path=row_dict["meta_path"], description=row_dict["description"] or "",
            source=row_dict["source"] or "manual",
        )


# Global singleton
_browser_manager: Optional[BrowserManager] = None


def get_browser_manager() -> BrowserManager:
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = BrowserManager()
    return _browser_manager
