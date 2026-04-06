"""Shared utilities for RDC server modules.

Contains lazy singleton accessors (to avoid circular imports),
common helpers for ANSI stripping, JSON safety, and enum handling.
"""

import json
import re
from typing import Any, Optional

# ── ANSI stripping ──

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[^[].?")


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


# ── JSON helpers ──

def safe_json_loads(data: Optional[str]) -> Optional[dict]:
    """Safely parse JSON, returning None on failure."""
    if not data:
        return None
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None


def json_field(data: Optional[dict]) -> Optional[str]:
    """Serialize a dict to JSON for DB storage, or None."""
    return json.dumps(data) if data else None


# ── Enum helpers ──

def enum_value(obj: Any) -> str:
    """Extract .value from enum or return str()."""
    return obj.value if hasattr(obj, "value") else str(obj)


# ── Lazy singleton accessors (avoid circular imports at module load) ──

def get_rdc_db():
    from .db.connection import get_db
    return get_db("rdc")


def get_channel_manager():
    from .channel_manager import get_channel_manager as _get
    return _get()


def get_terminal_manager():
    from .terminal import get_terminal_manager as _get
    return _get()


def get_state_machine():
    from .state_machine import get_state_machine as _get
    return _get()


def get_project_repo():
    from .db.repositories import get_project_repo as _get
    return _get()


def get_task_repo():
    from .db.repositories import get_task_repo as _get
    return _get()


def get_process_manager():
    from .processes import get_process_manager as _get
    return _get()


def get_conversation_manager():
    from .conversation import get_conversation_manager as _get
    return _get()


def get_rdc_home():
    from .config import get_rdc_home as _get
    return _get()
