"""Orchestrator intent engine — LLM-powered intent understanding + action execution.

Routes all user input (voice, mobile command bar, desktop chat) through a single
smart layer that uses tool calling for reliable structured output.
"""

import json as _json_mod
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions for the LLM (OpenAI-compatible function-calling schema)
# ---------------------------------------------------------------------------

ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate to a page in the dashboard. 'settings' = project settings, 'admin' = system/admin settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "string",
                        "enum": ["dashboard", "settings", "admin", "debug"],
                        "description": "Page to navigate to",
                    }
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_project",
            "description": "Select/focus on a project by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name (fuzzy matched)"}
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_projects",
            "description": "Open the project search dialog",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_project",
            "description": "Open the add project dialog (use only when user wants to browse for an existing project to connect)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "Create and scaffold a brand new project from a description. Use when the user wants to build something new.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name (short, kebab-case, e.g. 'wallet-scanner')"},
                    "description": {"type": "string", "description": "What the project does — used to infer tech stack and scaffold files"},
                },
                "required": ["name", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_collection",
            "description": "Switch to a project collection",
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "description": "Collection name (fuzzy matched)"}
                },
                "required": ["collection"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_process",
            "description": "Start a service action (dev server, database, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {"type": "string", "description": "Process ID to start"}
                },
                "required": ["process_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_action",
            "description": "Execute an action — works for both services (start) and one-shot commands (run). Prefer this over start_process for commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_id": {"type": "string", "description": "Action/process ID to execute"}
                },
                "required": ["action_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_process",
            "description": "Stop a running action/service",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {"type": "string", "description": "Process ID to stop"}
                },
                "required": ["process_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_terminal",
            "description": "Open a terminal for a project",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"}
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_terminal",
            "description": "Send text/command to the active terminal",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to send"}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_terminal",
            "description": "Focus/activate an existing terminal. Use when the user wants to switch to or view a specific terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name to find terminal for"},
                    "terminal_id": {"type": "string", "description": "Specific terminal ID (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_browser",
            "description": "Open the browser tab. Optionally navigate to a URL. Use when user says 'open browser', 'go to localhost', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate the browser to a URL using PinchTab automation. Use for 'go to localhost:3000', 'open google.com', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                    "tab_id": {"type": "string", "description": "Tab ID (optional, uses active tab)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "Get interactive element tree with ref numbers from the current page. Use for 'what's on the screen?', 'what do you see?', 'read the page'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "string", "description": "Tab ID (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an interactive element by its ref from a previous snapshot. Refs are strings like 'e27'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Element ref from snapshot (e.g. 'e27')"},
                    "tab_id": {"type": "string", "description": "Tab ID (optional)"},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": "Type text into an input element and press Enter to submit. Refs are strings like 'e27'. This is the primary way to search or submit forms — just fill the input and it auto-submits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Element ref from snapshot (e.g. 'e27')"},
                    "value": {"type": "string", "description": "Text to type in"},
                    "submit": {"type": "boolean", "description": "Press Enter after typing (default: true)"},
                    "tab_id": {"type": "string", "description": "Tab ID (optional)"},
                },
                "required": ["ref", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_text",
            "description": "Extract readable text content from the current page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {"type": "string", "description": "Tab ID (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_tabs",
            "description": "List all open browser tabs.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_eval",
            "description": "Run JavaScript in the PinchTab browser automation page. Only use when the user explicitly asks to execute JS in a browser tab. Do NOT use for general questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
                    "tab_id": {"type": "string", "description": "Tab ID (optional)"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_find",
            "description": "Find elements on the page by natural language description. Returns matching element refs with confidence scores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural language description of the element(s) to find, e.g. 'the search box' or 'the login button'"},
                    "tab_id": {"type": "string", "description": "Tab ID (optional)"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_input",
            "description": "Focus a specific input on the dashboard. Use when user wants to type in a specific area.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["terminal", "command_bar", "browser_url", "search"],
                        "description": "Which input to focus",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task for a project",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "description": {"type": "string", "description": "Task description"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_tab",
            "description": "Switch to a dashboard tab. Use 'processes' for actions (services and commands). Use 'browser' for browser sessions/recordings. Use 'activity' for audit log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab": {
                        "type": "string",
                        "enum": ["processes", "tasks", "browser", "contexts", "workers", "system", "chat", "pinchtab", "project", "activity"],
                        "description": "Tab to show. 'processes' = actions (services + commands), 'activity' = audit event log",
                    }
                },
                "required": ["tab"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_logs",
            "description": "Open the live logs panel. Can show system logs or logs for a specific action. To show action logs, pass the action/process ID exactly as shown in the actions list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "Process ID to show logs for (e.g. 'myproject-web'). Omit for system logs.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_activity",
            "description": "Open the activity / audit log panel showing recent system events. NOT for showing actions/services — use show_tab(tab='processes') for that.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_screenshots",
            "description": "Open the screenshots panel",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_preview",
            "description": "Open browser preview for a running service action",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {"type": "string", "description": "Process ID to preview"}
                },
                "required": ["process_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture a screenshot of a project's preview",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"}
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": "Spawn an AI coding agent for a project with a task",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "task": {"type": "string", "description": "Task description for the agent"},
                },
                "required": ["project", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "present_options",
            "description": "Show clickable options for the user to choose from. Use when offering choices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of options the user can click",
                    }
                },
                "required": ["options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_phone_call",
            "description": "End/hangup the current phone call when user says goodbye or wants to hang up",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pair_with_client",
            "description": "Pair this phone call with a dashboard client for remote control. If no client_name given, pairs with the first connected client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {
                        "type": "string",
                        "description": "Name of the client to pair with (fuzzy matched). Omit to auto-select.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unpair_client",
            "description": "Unpair the phone call from the dashboard client, returning to conversational mode.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enable_type_mode",
            "description": "Enable type mode: all subsequent speech is sent as raw text to the terminal (or specified input). Bypasses AI interpretation. User says 'type mode' or 'start typing'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["terminal"],
                        "description": "Input target. Default: terminal.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disable_type_mode",
            "description": "Disable type mode, return to normal voice chat.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_client",
            "description": "Rename this client/device to a friendly name (e.g. 'tesla', 'office-mac', 'phone')",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The new friendly name for this client",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_layout",
            "description": "Switch the dashboard layout. Use when user says 'switch to kiosk mode', 'use mobile layout', 'desktop mode', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "layout": {
                        "type": "string",
                        "enum": ["desktop", "mobile", "kiosk"],
                        "description": "Layout mode to switch to",
                    }
                },
                "required": ["layout"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_theme",
            "description": "Change the dashboard color theme. Use when user says 'dark theme', 'switch to modern', 'brutalist mode', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "enum": ["default", "modern", "brutalist"],
                        "description": "Theme to apply",
                    }
                },
                "required": ["theme"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_server",
            "description": "Restart the RDC server. Use when user says 'restart server', 'reload server', 'reboot RDC'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "server_status",
            "description": "Get current server health and status info.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_terminal",
            "description": "Kill/close a terminal session. If no terminal_id given, kills the current project's terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "Terminal session ID to kill (optional — defaults to current project terminal)"},
                    "project": {"type": "string", "description": "Project whose terminal to kill (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_terminal",
            "description": "Restart a terminal session. If no terminal_id given, restarts the current project's terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "Terminal session ID to restart (optional)"},
                    "project": {"type": "string", "description": "Project whose terminal to restart (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_sidebar",
            "description": "Toggle the sidebar panel open or closed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_chat",
            "description": "Toggle the chat panel open or closed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_process",
            "description": "Restart a running service action (stop then start).",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {"type": "string", "description": "Process ID to restart"},
                },
                "required": ["process_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_all_processes",
            "description": "Stop all running processes, optionally filtered to a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Only stop processes for this project (optional — omit to stop all)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_all_processes",
            "description": "Start all stopped processes, optionally filtered to a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Only start processes for this project (optional — omit to start all)"},
                },
            },
        },
    },
]

# Trimmed tool set for local models — fewer tools = less context = faster responses
_LOCAL_TOOL_NAMES = {
    "navigate", "select_project", "show_tab", "open_terminal",
    "start_process", "stop_process", "execute_action", "create_task", "create_project",
    "spawn_agent", "present_options", "select_collection",
    "set_layout", "set_theme", "restart_server", "restart_process",
    "kill_terminal", "restart_terminal", "toggle_sidebar", "toggle_chat",
}
ORCHESTRATOR_TOOLS_LOCAL = [t for t in ORCHESTRATOR_TOOLS if t["function"]["name"] in _LOCAL_TOOL_NAMES]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    name: str
    params: dict


@dataclass
class IntentResult:
    response: str
    actions: list[ToolCall] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


@dataclass
class ProjectInfo:
    name: str
    description: Optional[str] = None
    collection: Optional[str] = None
    collection_id: Optional[str] = None


@dataclass
class OrchestratorContext:
    project: Optional[str] = None
    collection: Optional[str] = None
    projects: list[str] = field(default_factory=list)
    project_details: list[ProjectInfo] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    processes: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    terminals: list[dict] = field(default_factory=list)
    agents: list[dict] = field(default_factory=list)
    contexts: list[dict] = field(default_factory=list)
    terminal_open: bool = False
    channel: str = "desktop"
    connected_clients: list[dict] = field(default_factory=list)
    active_call_sid: Optional[str] = None
    client_id: Optional[str] = None
    paired_client_id: Optional[str] = None
    paired_client_name: Optional[str] = None
    project_profile: Optional[dict] = None
    pinchtab_available: bool = False
    pinchtab_tabs: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def fuzzy_match(query: str, candidates: list[str]) -> Optional[str]:
    """Fuzzy match a query against a list of candidates."""
    if not query or not candidates:
        return None

    q = query.lower().strip().replace(" ", "-")

    # Exact match
    for c in candidates:
        if c.lower() == q:
            return c

    # Substring match
    matches = [c for c in candidates if q in c.lower()]
    if len(matches) == 1:
        return matches[0]

    # Partial word match — query words all appear somewhere in candidate
    q_words = query.lower().split()
    if q_words:
        word_matches = []
        for c in candidates:
            cl = c.lower()
            if all(w in cl for w in q_words):
                word_matches.append(c)
        if len(word_matches) == 1:
            return word_matches[0]
        if word_matches:
            # Return shortest match (most specific)
            return min(word_matches, key=len)

    # If substring found multiple, return shortest
    if matches:
        return min(matches, key=len)

    return None


def fuzzy_match_process(query: str, processes: list[dict]) -> Optional[str]:
    """Fuzzy match a process query against available processes."""
    ids = [p.get("id", "") for p in processes if p.get("id")]
    names = [p.get("name", "") for p in processes if p.get("name")]

    # Try matching against IDs first (more specific)
    result = fuzzy_match(query, ids)
    if result:
        return result

    # Try matching against names, then map back to ID
    name_match = fuzzy_match(query, names)
    if name_match:
        for p in processes:
            if p.get("name") == name_match:
                return p.get("id", name_match)

    return None


# ---------------------------------------------------------------------------
# Nanobot config — global orchestrator settings stored in ~/.rdc/nanobot.json
# ---------------------------------------------------------------------------

AVAILABLE_MODELS = [
    {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash", "tier": "fast"},
    {"id": "minimax/minimax-m2.5", "name": "MiniMax M2.5", "tier": "fast"},
    {"id": "minimax/minimax-m2.1", "name": "MiniMax M2.1", "tier": "fast"},
    {"id": "anthropic/claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "tier": "fast"},
    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "tier": "mid"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "tier": "fast"},
    {"id": "openai/gpt-4o", "name": "GPT-4o", "tier": "mid"},
]

DEFAULT_NANOBOT_CONFIG = {
    "model_fast": "google/gemini-2.0-flash-001",
    "model_mid": "anthropic/claude-sonnet-4",
    "word_threshold": 12,  # Messages with <= this many words use fast model
    "max_tokens": 400,
    "compress_enabled": False,  # Enable LLMLingua-2 prompt compression
    "compress_rate": 0.5,      # Keep this fraction of tokens (0.5 = 50%)
    "projects_base_path": "",  # Base directory for new projects (empty = ~/projects)
    "ollama_model": "qwen3.5",  # Local Ollama model name
    "llm_provider": "cloud",    # "cloud" (OpenRouter/OpenAI) or "ollama" (local)
    "routing_mode": "auto",     # "auto" (complexity-based) or "manual" (word-count)
    "model_overrides": {},      # e.g. {"model_reasoning": "anthropic/claude-sonnet-4"}
}


def _nanobot_config_path() -> Path:
    from .config import get_rdc_home
    return get_rdc_home() / "nanobot.json"


def load_nanobot_config() -> dict:
    """Load nanobot config from disk."""
    path = _nanobot_config_path()
    if path.exists():
        try:
            with open(path) as f:
                stored = _json_mod.load(f)
            # Merge with defaults for any missing keys
            merged = {**DEFAULT_NANOBOT_CONFIG, **stored}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_NANOBOT_CONFIG)


def save_nanobot_config(config: dict) -> dict:
    """Save nanobot config to disk. Returns the saved config."""
    path = _nanobot_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT_NANOBOT_CONFIG, **config}
    with open(path, "w") as f:
        _json_mod.dump(merged, f, indent=2)
    return merged


# ---------------------------------------------------------------------------
# Layer 1: JSONL audit log — every interaction, by day
# ---------------------------------------------------------------------------

def _nanobot_log_dir() -> Path:
    from .config import get_rdc_home
    d = get_rdc_home() / "nanobot_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nanobot_log_path(day: date) -> Path:
    return _nanobot_log_dir() / f"nanobot_{day.isoformat()}.jsonl"


def log_nanobot_interaction(
    *,
    channel: str,
    project: Optional[str],
    message: str,
    response: str,
    actions: list[dict],
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: int = 0,
    flags: list[str] | None = None,
) -> None:
    """Append a nanobot interaction to today's JSONL log file."""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "channel": channel,
        "project": project,
        "message": message,
        "response": response,
        "actions": actions,
        "model": model,
        "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
        "duration_ms": duration_ms,
        "flags": flags or [],
    }
    try:
        path = _nanobot_log_path(date.today())
        with open(path, "a") as f:
            f.write(_json_mod.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to write nanobot log")


def load_recent_history(n: int = 5) -> list[dict]:
    """Load the last N nanobot interactions from today + yesterday."""
    entries: list[dict] = []
    for day in [date.today() - timedelta(days=1), date.today()]:
        path = _nanobot_log_path(day)
        if not path.exists():
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(_json_mod.loads(line))
                    except (ValueError, TypeError):
                        continue
        except Exception:
            logger.exception("Failed to read nanobot log %s", path)
    return entries[-n:] if len(entries) > n else entries


# ---------------------------------------------------------------------------
# Layer 2: Per-project memory — curated knowledge
# ---------------------------------------------------------------------------

def _project_memory_dir(project_name: str) -> Path:
    from .config import get_rdc_home
    d = get_rdc_home() / "project_memory" / project_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _project_memory_path(project_name: str) -> Path:
    return _project_memory_dir(project_name) / "memory.jsonl"


def append_project_memory(project: str, entry: dict) -> None:
    """Append a knowledge entry to a project's memory."""
    entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    entry.setdefault("source", "nanobot")
    try:
        path = _project_memory_path(project)
        with open(path, "a") as f:
            f.write(_json_mod.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to write project memory for %s", project)


def load_project_memory(project: str, n: int = 10) -> list[dict]:
    """Load last N knowledge entries for a project."""
    path = _project_memory_path(project)
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(_json_mod.loads(line))
                except (ValueError, TypeError):
                    continue
    except Exception:
        logger.exception("Failed to read project memory for %s", project)
    return entries[-n:] if len(entries) > n else entries


KNOWLEDGE_EXTRACTION_PROMPT = """You are a knowledge extractor for a software project management system.
Given a user message and assistant response, extract key facts worth remembering about the project.

Return ONLY valid JSON (no markdown fences):
{"worth_saving": true/false, "type": "conversation|decision|user_story|task|design|bug|feature", "summary": "one line summary", "details": "optional extra context or empty string"}

If the interaction is just a simple command (start process, navigate, switch tabs, select project, etc.) with no knowledge value, return:
{"worth_saving": false}"""


async def extract_knowledge(
    message: str, response: str, project: str, actions: list[dict]
) -> None:
    """Extract key facts from an interaction and append to project memory.

    Runs as a fire-and-forget async task — never blocks the orchestrator response.
    """
    import asyncio

    # Quick filter: skip pure navigation/command actions with no conversational content
    action_only_names = {"navigate", "show_tab", "show_logs", "show_screenshots",
                         "search_projects", "select_project", "select_collection"}
    if actions:
        action_names = {a.get("action", "") for a in actions}
        if action_names and action_names.issubset(action_only_names):
            return

    try:
        engine = get_intent_engine()
        client = engine._get_client()

        user_content = f"User: {message}\nAssistant: {response}"
        if actions:
            action_strs = [a.get("action", "?") for a in actions]
            user_content += f"\nActions executed: {', '.join(action_strs)}"

        ke_model = select_model("")  # short message → fast model (or ollama model)

        def _call():
            return client.chat.completions.create(
                model=ke_model,
                messages=[
                    {"role": "system", "content": KNOWLEDGE_EXTRACTION_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=200,
            )

        resp = await asyncio.to_thread(_call)
        text = (resp.choices[0].message.content or "").strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = _json_mod.loads(text)
        if not parsed.get("worth_saving"):
            return

        append_project_memory(project, {
            "type": parsed.get("type", "conversation"),
            "summary": parsed.get("summary", ""),
            "details": parsed.get("details", ""),
        })
    except Exception:
        logger.debug("Knowledge extraction failed for %s", project, exc_info=True)


# ---------------------------------------------------------------------------
# Git activity
# ---------------------------------------------------------------------------

def fetch_git_activity(project_path: str, n: int = 5) -> list[str]:
    """Fetch last N git commits from a project directory. Returns [] on failure."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return []
        return [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    except Exception:
        logger.debug("Git log failed for %s", project_path, exc_info=True)
        return []


def _resolve_project_path(project_name: Optional[str]) -> Optional[str]:
    """Resolve a project name to its filesystem path."""
    if not project_name:
        return None
    try:
        from .db.repositories import get_project_repo
        repo = get_project_repo()
        proj = repo.get(project_name)
        return proj.path if proj else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Prompt compression (LLMLingua-2)
# ---------------------------------------------------------------------------

_compressor = None
_compressor_failed = False


def _get_compressor():
    """Lazy-load the LLMLingua-2 prompt compressor (singleton)."""
    global _compressor, _compressor_failed
    if _compressor is not None:
        return _compressor
    if _compressor_failed:
        return None
    try:
        from llmlingua import PromptCompressor
        _compressor = PromptCompressor(
            "microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
            use_llmlingua2=True,
            device_map="cpu",
        )
        logger.info("LLMLingua-2 compressor loaded")
        return _compressor
    except Exception:
        _compressor_failed = True
        logger.info("LLMLingua-2 not available — running without compression")
        return None


def compress_context(text: str, rate: float = 0.5) -> str:
    """Compress a text block using LLMLingua-2. Returns original on failure."""
    compressor = _get_compressor()
    if compressor is None or not text.strip():
        return text
    try:
        result = compressor.compress_prompt(
            [text],
            rate=rate,
            force_tokens=["\n", "?", ":", "[", "]", "(", ")"],
        )
        compressed = result.get("compressed_prompt", text)
        original_tokens = result.get("origin_tokens", 0)
        compressed_tokens = result.get("compressed_tokens", 0)
        if original_tokens:
            logger.debug(
                "Prompt compressed: %d -> %d tokens (%.0f%%)",
                original_tokens, compressed_tokens,
                100 * compressed_tokens / original_tokens,
            )
        return compressed
    except Exception:
        logger.debug("Compression failed, using original", exc_info=True)
        return text


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

def select_model(message: str) -> str:
    """Select LLM model based on message complexity and nanobot config.

    Legacy entry point — delegates to ModelRouter for auto mode.
    """
    return _get_model_router().select(message)


# ---------------------------------------------------------------------------
# Complexity estimation & auto model routing
# ---------------------------------------------------------------------------

import re as _re

# Regex patterns for trivial UI commands
_TRIVIAL_PATTERNS = _re.compile(
    r"^("
    r"show\s+(dashboard|logs|activity|screenshots|browser|tasks?|processes|chat|system|project|pinchtab)"
    r"|open\s+(browser|terminal|settings|admin|preview)"
    r"|go\s+to\s+(dashboard|settings|admin|debug)"
    r"|switch\s+to\s+\w+"
    r"|toggle\s+(sidebar|chat)"
    r"|dark\s+mode|light\s+mode"
    r"|focus\s+(terminal|input|search|command)"
    r"|search\s+projects?"
    r"|show\s+tab"
    r")$",
    _re.IGNORECASE,
)

_REASONING_KEYWORDS = _re.compile(
    r"\b(explain|why|how\s+does|compare|analyze|debug|plan|what\s+went\s+wrong|"
    r"difference\s+between|pros?\s+and\s+cons?|trade.?offs?|reason|understand|evaluate)\b",
    _re.IGNORECASE,
)

_COMPLEX_KEYWORDS = _re.compile(
    r"\b(create|build|implement|spawn|configure|set\s*up|scaffold|generate|deploy|migrate|refactor)\b",
    _re.IGNORECASE,
)

# Acceptable cost tiers per complexity tier
# Note: "free" excluded — free models on OpenRouter often have data policy
# restrictions that cause 404 errors.
_TIER_COST_RANGES: dict[str, list[str]] = {
    "trivial":   ["cheap"],
    "simple":    ["cheap", "moderate"],
    "complex":   ["moderate", "expensive"],
    "reasoning": ["expensive", "premium"],
}


def _estimate_complexity(message: str, conversation_depth: int = 0) -> str:
    """Estimate the complexity tier of a user message using heuristics.

    Returns one of: "trivial", "simple", "complex", "reasoning".
    """
    text = message.strip()
    words = text.split()
    word_count = len(words)

    # 1. Regex match trivial UI patterns
    if _TRIVIAL_PATTERNS.match(text):
        return "trivial"

    # 2. Very short → simple
    if word_count <= 5 and not _REASONING_KEYWORDS.search(text):
        return "simple"

    # 3. Reasoning keywords or long + deep conversation → reasoning
    if _REASONING_KEYWORDS.search(text):
        return "reasoning"
    if word_count > 30 and conversation_depth > 5:
        return "reasoning"

    # 4. Complex keywords or deep conversation or long message → complex
    if _COMPLEX_KEYWORDS.search(text):
        return "complex"
    if conversation_depth > 5:
        return "complex"
    if word_count > 25:
        return "complex"

    # 5. Medium-length without special keywords → simple
    if word_count <= 12:
        return "simple"

    # 6. Default
    return "complex"


class ModelRouter:
    """Routes messages to the cheapest capable model based on complexity."""

    def __init__(self):
        self._models_cache: list[dict] | None = None

    def invalidate(self):
        """Clear cached models so they're reloaded on next select()."""
        self._models_cache = None

    def _load_models(self) -> list[dict]:
        """Load models from disk cache (populated by /models?refresh=true)."""
        if self._models_cache is not None:
            return self._models_cache
        try:
            from .config import get_rdc_home
            cache_path = get_rdc_home() / "data" / "models_cache.json"
            if cache_path.exists():
                import json
                data = json.loads(cache_path.read_text())
                self._models_cache = data.get("models") or []
            else:
                self._models_cache = []
        except Exception:
            self._models_cache = []
        return self._models_cache

    def select(self, message: str, conversation_depth: int = 0) -> str:
        """Select the best model for a message.

        1. ollama → return ollama model (bypass)
        2. routing_mode == "manual" → legacy word-count selection
        3. Auto: estimate complexity, check overrides, pick from cache
        """
        cfg = load_nanobot_config()

        # Ollama bypass
        if cfg.get("llm_provider") == "ollama":
            return cfg.get("ollama_model", "qwen3.5")

        # Manual mode → legacy behavior
        if cfg.get("routing_mode", "auto") == "manual":
            return self._legacy_select(message, cfg)

        # Auto mode
        tier = _estimate_complexity(message, conversation_depth)
        logger.debug("Auto-routing: tier=%s message=%r", tier, message[:80])

        # Check tier override in config
        overrides = cfg.get("model_overrides") or {}
        override_key = f"model_{tier}"
        if overrides.get(override_key):
            return overrides[override_key]

        # Try to pick from models cache
        models = self._load_models()
        if not models:
            # No cache → fall back to legacy models
            return self._legacy_select_for_tier(tier, cfg)

        return self._pick_model(models, tier, cfg)

    def _legacy_select(self, message: str, cfg: dict) -> str:
        """Original word-count based selection."""
        threshold = cfg.get("word_threshold", 12)
        if len(message.split()) <= threshold:
            return cfg.get("model_fast", DEFAULT_NANOBOT_CONFIG["model_fast"])
        return cfg.get("model_mid", DEFAULT_NANOBOT_CONFIG["model_mid"])

    def _legacy_select_for_tier(self, tier: str, cfg: dict) -> str:
        """Map tier to legacy fast/mid when no models cache is available."""
        if tier in ("trivial", "simple"):
            return cfg.get("model_fast", DEFAULT_NANOBOT_CONFIG["model_fast"])
        return cfg.get("model_mid", DEFAULT_NANOBOT_CONFIG["model_mid"])

    def _pick_model(self, models: list[dict], tier: str, cfg: dict) -> str:
        """Pick the best model from the cache for a given complexity tier."""
        acceptable_costs = _TIER_COST_RANGES.get(tier, ["moderate", "expensive"])
        prefers_reasoning = tier == "reasoning"

        # Filter: must have tools, cost in acceptable range
        candidates = [
            m for m in models
            if m.get("has_tools") and m.get("cost_tier") in acceptable_costs
        ]

        if not candidates:
            # Widen: accept any model with tools
            candidates = [m for m in models if m.get("has_tools")]

        if not candidates:
            return self._legacy_select_for_tier(tier, cfg)

        # For reasoning tier, prefer models with reasoning capability
        if prefers_reasoning:
            reasoning_candidates = [m for m in candidates if m.get("has_reasoning")]
            if reasoning_candidates:
                candidates = reasoning_candidates

        # Preferred providers (more reliable)
        preferred_providers = {"anthropic", "openai", "google"}

        # Sort: for trivial/simple → cheapest first; for complex/reasoning → most capable first
        cost_order = {"free": 0, "cheap": 1, "moderate": 2, "expensive": 3, "premium": 4}
        if tier in ("trivial", "simple"):
            candidates.sort(key=lambda m: (
                cost_order.get(m.get("cost_tier", "moderate"), 2),
                0 if m.get("provider", "") in preferred_providers else 1,
            ))
        else:
            candidates.sort(key=lambda m: (
                0 if m.get("provider", "") in preferred_providers else 1,
                -cost_order.get(m.get("cost_tier", "moderate"), 2),
                -1 if m.get("has_reasoning") else 0,
            ))

        selected = candidates[0]["id"]
        logger.debug("Auto-routing selected: %s (tier=%s, candidates=%d)", selected, tier, len(candidates))
        return selected


_model_router: Optional[ModelRouter] = None


def _get_model_router() -> ModelRouter:
    global _model_router
    if _model_router is None:
        _model_router = ModelRouter()
    return _model_router


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(
    ctx: OrchestratorContext,
    recent_history: list[dict] | None = None,
    project_memory: list[dict] | None = None,
    git_commits: list[str] | None = None,
    thread_summary: str | None = None,
    current_context: str | None = None,
) -> str:
    """Build a system prompt with static instructions + compressed dynamic context."""

    # === STATIC INSTRUCTIONS (never compressed) ===
    instructions = [
        "You are the RDC Command Center orchestrator. You understand user intent and execute actions via tool calls.",
        "Be concise. Execute actions, then briefly confirm what you did.",
        "You have memory of recent conversations and project knowledge — use it to answer questions.",
        "",
        "Use tool calls to execute actions. If the user's message is conversational "
        "(not a command), just respond naturally without tool calls.",
        "IMPORTANT: Only use browser_eval/browser_snapshot/browser_click tools when the user explicitly "
        "asks to interact with a browser tab. For general questions, answer from context data.",
    ]

    if ctx.channel == "phone_paired":
        paired_desc = f"paired with client: {ctx.paired_client_name}" if ctx.paired_client_name else "paired with a dashboard client"
        instructions.extend([
            "",
            f"User is on a PHONE CALL {paired_desc}. You have FULL remote control.",
            "You can execute ALL actions (show_tab, navigate, open_terminal, send_to_terminal, etc.)",
            "— they will be sent to the paired dashboard. Keep responses concise (1-2 sentences).",
            "The user can see their screen, so execute UI actions and briefly confirm.",
            "",
            "When the user requests an ACTION (switch project, open terminal, show logs, create task, etc.), "
            "ALWAYS use the appropriate tool call. Do NOT just describe what you would do — execute it.",
            "",
            "When the user asks a QUESTION (how many tasks, what project am I on, what's running, etc.), "
            "answer it directly from the context data below. You have full visibility into projects, "
            "processes, tasks, terminals, agents, and captured contexts — read the data and answer verbally.",
            "",
            "Use `enable_type_mode` when user says 'type mode', 'start typing', or 'dictation mode'.",
            "This sends all subsequent speech as raw text to the terminal. User says 'exit type mode' or 'chat mode' to return.",
        ])
    elif ctx.channel == "phone":
        client_names = [c.get("client_name", c.get("client_id", "?")) for c in ctx.connected_clients]
        pairing_hint = ""
        if client_names:
            pairing_hint = f"\nConnected clients available for pairing: {', '.join(client_names)}. "
            pairing_hint += "If user wants to control the dashboard, use pair_with_client to pair with a client."
        instructions.extend([
            "",
            "IMPORTANT: User is on a PHONE CALL. You must respond CONVERSATIONALLY with spoken answers.",
            "DO NOT use tool calls to show tabs, navigate, or control the UI — the user cannot see a screen.",
            "Instead, READ the context data below and TELL the user the answer verbally.",
            "You have full visibility into: projects, processes, tasks, terminals, agents, and captured browser contexts.",
            "When asked about any of these, count them, list them, describe their status — answer from the data.",
            "Only use tool calls for server-side actions (start_process, stop_process, create_task, spawn_agent, end_phone_call, pair_with_client).",
            "Keep responses concise (2-3 sentences max) since this is a phone conversation.",
            "If the user wants UI actions (open terminal, show tabs, etc.) suggest pairing with a dashboard client first.",
            pairing_hint,
        ])
    elif ctx.channel == "voice":
        instructions.append("User is speaking via voice. Keep responses very short (1-2 sentences).")
    elif ctx.channel == "mobile":
        instructions.append("User is on mobile. Keep responses brief.")

    # === DYNAMIC CONTEXT (compressible) ===
    context_parts: list[str] = []

    # Older conversation summary (compacted thread history)
    if thread_summary:
        context_parts.append("Previous conversation context:")
        context_parts.append(thread_summary[:2000])
        context_parts.append("")

    # Conversation memory
    if recent_history:
        context_parts.append("Recent conversation history:")
        for entry in recent_history:
            ts = entry.get("ts", "?")[:16]
            proj = entry.get("project") or "none"
            msg = entry.get("message", "")[:120]
            resp = entry.get("response", "")[:120]
            action_names = [a.get("action", "?") for a in entry.get("actions", [])]
            action_str = f" -> [{', '.join(action_names)}]" if action_names else ""
            context_parts.append(f"  [{ts}] ({proj}) User: {msg}")
            context_parts.append(f"    Nanobot: {resp}{action_str}")
        context_parts.append("")

    # Project knowledge
    if project_memory:
        context_parts.append(f"Project knowledge ({ctx.project or 'active'}):")
        for entry in project_memory:
            etype = entry.get("type", "note")
            summary = entry.get("summary", "")[:100]
            context_parts.append(f"  [{etype}] {summary}")
        context_parts.append("")

    # Git activity
    if git_commits:
        context_parts.append(f"Recent git activity ({ctx.project or 'active project'}):")
        for commit in git_commits:
            context_parts.append(f"  {commit}")
        context_parts.append("")

    # Current state
    if ctx.project:
        context_parts.append(f"Active project: {ctx.project}")
    if ctx.collection:
        context_parts.append(f"Active collection: {ctx.collection}")

    # Projects
    if ctx.project_details:
        proj_lines = []
        for p in ctx.project_details:
            desc = f" — {p.description}" if p.description else ""
            col = f" [{p.collection}]" if p.collection else ""
            proj_lines.append(f"  {p.name}{col}{desc}")
        context_parts.append("Projects:\n" + "\n".join(proj_lines))
    elif ctx.projects:
        context_parts.append(f"Projects: {', '.join(ctx.projects)}")

    # Collections
    if ctx.collections:
        context_parts.append(f"Collections: {', '.join(ctx.collections)}")

    # Project Profile
    if ctx.project_profile:
        pp = ctx.project_profile
        profile_lines = ["Project Profile:"]
        if pp.get("purpose"):
            profile_lines.append(f"  Purpose: {pp['purpose']}")
        if pp.get("stack"):
            profile_lines.append(f"  Stack: {', '.join(pp['stack'])}")
        if pp.get("conventions"):
            profile_lines.append(f"  Conventions: {pp['conventions']}")
        if pp.get("test_command"):
            profile_lines.append(f"  Test command: {pp['test_command']}")
        if pp.get("source_dir"):
            profile_lines.append(f"  Source dir: {pp['source_dir']}")
        if pp.get("test_dir"):
            profile_lines.append(f"  Test dir: {pp['test_dir']}")
        context_parts.append("\n".join(profile_lines))

    # Actions (services + commands)
    if ctx.processes:
        proc_lines = []
        for p in ctx.processes:
            status = p.get("status", "unknown")
            pid = p.get("id", "?")
            name = p.get("name", pid)
            kind = p.get("kind", "service")
            port = p.get("port")
            port_str = f" (port {port})" if port else ""
            proc_lines.append(f"  {pid}: {name} [{status}] ({kind}){port_str}")
        context_parts.append("Actions (services + commands, shown on 'processes' tab):\n" + "\n".join(proc_lines))

    # Tasks
    if ctx.tasks:
        task_lines = []
        for t in ctx.tasks:
            proj = t.get("project") or "—"
            title = t.get("title") or t.get("description", "?")[:60]
            status = t.get("status", "?")
            task_lines.append(f"  {t['id']}: [{status}] {proj} — {title}")
        context_parts.append(f"Tasks ({len(ctx.tasks)}):\n" + "\n".join(task_lines))

    # Terminals
    if ctx.terminals:
        term_lines = []
        for t in ctx.terminals:
            waiting = " (WAITING FOR INPUT)" if t.get("waiting_for_input") else ""
            cmd = t.get("command") or "shell"
            term_lines.append(f"  {t['id'][:8]}: {t.get('project', '?')} [{t.get('status', '?')}] {cmd}{waiting}")
        context_parts.append(f"Open terminals ({len(ctx.terminals)}):\n" + "\n".join(term_lines))
    elif ctx.terminal_open:
        context_parts.append("A terminal is currently open.")

    # Agents
    if ctx.agents:
        agent_lines = []
        for a in ctx.agents:
            agent_lines.append(f"  {a.get('project', '?')}: [{a.get('status', '?')}] ({a.get('provider', '?')})")
        context_parts.append(f"Agents ({len(ctx.agents)}):\n" + "\n".join(agent_lines))

    # Captured contexts (browser snapshots)
    if ctx.contexts:
        ctx_lines = []
        for c in ctx.contexts:
            title = c.get("title") or c.get("url") or c.get("id", "?")
            ctx_lines.append(f"  {c['id'][:8]}: {title}")
        context_parts.append(f"Captured contexts ({len(ctx.contexts)}):\n" + "\n".join(ctx_lines))

    # PinchTab browser automation
    if ctx.pinchtab_available:
        context_parts.append("Browser automation (PinchTab): available")
        if ctx.pinchtab_tabs:
            tab_lines = []
            for t in ctx.pinchtab_tabs:
                tab_lines.append(f"  {t.get('id', '?')}: {t.get('title', 'Untitled')} — {t.get('url', '')}")
            context_parts.append("Open browser tabs:\n" + "\n".join(tab_lines))
        context_parts.append("Use browser_snapshot to get element refs, then browser_click/browser_fill with those refs.")

    # Connected clients / caller info
    if ctx.client_id:
        context_parts.append(f"This client ID: {ctx.client_id}")
    if ctx.connected_clients:
        client_names = [c.get("client_name") or c.get("client_id", "?") for c in ctx.connected_clients]
        context_parts.append(f"Connected dashboard clients: {', '.join(client_names)}")

    context_parts.append(f"Channel: {ctx.channel}")

    # Current context from event synthesizer
    if current_context:
        context_parts.append("")
        context_parts.append("## Current Context")
        context_parts.append(current_context)

    # === COMPRESS dynamic context if enabled ===
    context_text = "\n".join(context_parts)

    cfg = load_nanobot_config()
    if cfg.get("compress_enabled") and context_text.strip():
        rate = cfg.get("compress_rate", 0.5)
        context_text = compress_context(context_text, rate=rate)

    return "\n".join(instructions) + "\n\n" + context_text


# ---------------------------------------------------------------------------
# Intent Engine
# ---------------------------------------------------------------------------

class IntentEngine:
    """LLM-powered intent understanding + action execution."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        cfg = load_nanobot_config()
        provider = cfg.get("llm_provider", "cloud")

        # Invalidate cached client if provider changed
        if self._client is not None and getattr(self, "_provider", None) != provider:
            self._client = None

        if self._client is not None:
            return self._client

        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package not installed")

        if provider == "ollama":
            self._client = OpenAI(
                api_key="ollama",
                base_url="http://localhost:11434/v1",
            )
        else:
            from .vault import get_secret

            api_key = (
                get_secret("OPENROUTER_API_KEY")
                or get_secret("OPENAI_API_KEY")
                or os.getenv("OPENROUTER_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            )
            if not api_key:
                raise RuntimeError("OPENROUTER_API_KEY or OPENAI_API_KEY not configured")

            self._client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        self._provider = provider
        return self._client

    async def process(self, message: str, ctx: OrchestratorContext, *, conversation_history: list[dict] | None = None) -> IntentResult:
        """Understand intent and return response + actions."""
        import asyncio

        start_time = time.monotonic()

        client = self._get_client()
        cfg = load_nanobot_config()
        is_local = cfg.get("llm_provider") == "ollama"

        # Estimate conversation depth for auto-routing
        conv_depth = len(conversation_history) if conversation_history else 0
        model = _get_model_router().select(message, conversation_depth=conv_depth)
        max_tokens = cfg.get("max_tokens", 400)

        # Gather conversation memory, project knowledge, and git context
        # Use smaller context windows for local models to keep latency down
        history_n = 2 if is_local else 5
        memory_n = 3 if is_local else 10
        turns_n = 5 if is_local else 20

        recent_history = load_recent_history(n=history_n)
        proj_memory = load_project_memory(ctx.project, n=memory_n) if ctx.project else []
        project_path = _resolve_project_path(ctx.project)
        git_commits = fetch_git_activity(project_path) if (project_path and not is_local) else []

        # Load thread turns from server-side conversation
        from .conversation import get_conversation_manager
        from .context_synthesizer import get_context_synthesizer
        conv_mgr = get_conversation_manager()
        thread_id = conv_mgr.get_or_create_thread(ctx.project)
        thread = conv_mgr.get_thread(thread_id)
        thread_turns = conv_mgr.get_recent_turns(thread_id, n=turns_n)

        # Synthesize context from events + conversation (skip for local models)
        current_context_str = None
        if not is_local:
            synthesizer = get_context_synthesizer()
            synthesized = synthesizer.synthesize(ctx.project, thread_id, ctx.client_id)
            current_context_str = synthesizer.format_for_prompt(synthesized)

        system_prompt = build_system_prompt(
            ctx,
            recent_history=recent_history,
            project_memory=proj_memory or None,
            git_commits=git_commits or None,
            thread_summary=thread.get("summary") if thread else None,
            current_context=current_context_str or None,
        )

        # Build messages: system + thread turns (server-side) + current user message
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        # Prefer server-side thread turns; fall back to client-sent history for backward compat
        turns_to_use = thread_turns if thread_turns else (conversation_history or [])
        for turn in turns_to_use:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        # Use trimmed tool set for local models to save context window
        tools = ORCHESTRATOR_TOOLS_LOCAL if is_local else ORCHESTRATOR_TOOLS

        # Remove browser tools when PinchTab is not available — prevents LLM
        # from hallucinating browser actions that will 404
        if not ctx.pinchtab_available:
            _BROWSER_TOOLS = {"browser_navigate", "browser_snapshot", "browser_click", "browser_fill", "browser_tabs", "browser_eval", "browser_find"}
            tools = [t for t in tools if t["function"]["name"] not in _BROWSER_TOOLS]

        # Run the synchronous OpenAI call in a thread to avoid blocking
        def _call():
            return client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )

        response = await asyncio.to_thread(_call)

        choice = response.choices[0]
        msg = choice.message

        # Extract tool calls
        actions: list[ToolCall] = []
        options: list[str] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    params = _json_mod.loads(tc.function.arguments) if tc.function.arguments else {}
                except (ValueError, TypeError):
                    params = {}
                # present_options → extract into options list, not an action
                if tc.function.name == "present_options":
                    options = params.get("options", [])
                else:
                    actions.append(ToolCall(name=tc.function.name, params=params))

        # Response text (may be None if only tool calls)
        response_text = msg.content or ""
        if not response_text and actions:
            response_text = _auto_confirm(actions)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "model": model,
            }
        usage["duration_ms"] = duration_ms

        return IntentResult(response=response_text, actions=actions, options=options, usage=usage)


def _auto_confirm(actions: list[ToolCall]) -> str:
    """Generate a brief, conversational confirmation for tool calls."""
    parts = []
    for a in actions:
        match a.name:
            case "navigate":
                parts.append(f"Taking you to {a.params.get('page', 'that page')}")
            case "select_project":
                parts.append(f"Switched to {a.params.get('project', 'the project')}")
            case "select_collection":
                parts.append(f"Switched to the {a.params.get('collection', '')} collection")
            case "start_process":
                parts.append(f"Starting {a.params.get('process_id', 'the process')}")
            case "stop_process":
                parts.append(f"Stopping {a.params.get('process_id', 'the process')}")
            case "execute_action":
                parts.append(f"Executing {a.params.get('action_id', 'the action')}")
            case "create_task":
                desc = a.params.get("description", "")[:60]
                parts.append(f"Created a task: {desc}")
            case "open_terminal":
                parts.append(f"Opening a terminal for {a.params.get('project', 'the project')}")
            case "show_tab":
                tab = a.params.get("tab", "?")
                parts.append(f"Here's the {tab} view")
            case "show_logs":
                proc = a.params.get("process_id") or a.params.get("process")
                parts.append(f"Pulling up logs for {proc}" if proc else "Here are the system logs")
            case "show_activity":
                parts.append("Here's the activity log")
            case "show_screenshots":
                parts.append("Here are the screenshots")
            case "spawn_agent":
                parts.append(f"Spawning an agent for {a.params.get('project', 'the project')}")
            case "search_projects":
                parts.append("Here's the project search")
            case "add_project":
                parts.append("Opening the add project dialog")
            case "create_project":
                parts.append(f"Creating {a.params.get('name', 'the project')}")
            case "end_phone_call":
                parts.append("Goodbye!")
            case "pair_with_client":
                parts.append(f"Paired with {a.params.get('client_name', 'the client')}")
            case "unpair_client":
                parts.append("Unpaired from the dashboard")
            case "enable_type_mode":
                parts.append("Type mode on — speaking to terminal now")
            case "rename_client":
                parts.append(f"Renamed this device to {a.params.get('name', '?')}")
            case "disable_type_mode":
                parts.append("Back to chat mode")
            case "focus_terminal":
                parts.append("Focused the terminal")
            case "open_browser":
                url = a.params.get("url")
                parts.append(f"Opening browser at {url}" if url else "Opening the browser")
            case "focus_input":
                parts.append(f"Focused the {a.params.get('target', 'input')}")
            case "browser_navigate":
                parts.append(f"Navigating to {a.params.get('url', 'the page')}")
            case "browser_snapshot":
                parts.append("Getting page snapshot")
            case "browser_click":
                parts.append(f"Clicking element #{a.params.get('ref', '?')}")
            case "browser_fill":
                parts.append(f"Filling element #{a.params.get('ref', '?')}")
            case "browser_text":
                parts.append("Extracting page text")
            case "browser_tabs":
                parts.append("Listing browser tabs")
            case "browser_eval":
                parts.append("Running JavaScript")
            case _:
                parts.append(f"Done — {a.name.replace('_', ' ')}")
    return ". ".join(parts) + "." if parts else "Done."


# ---------------------------------------------------------------------------
# Action Executor
# ---------------------------------------------------------------------------

class ActionExecutor:
    """Maps tool call results to actual system calls."""

    async def execute(
        self, action_name: str, params: dict, ctx: OrchestratorContext
    ) -> dict:
        """Execute a single action, return result dict."""
        try:
            match action_name:
                # --- Client-side actions (returned for frontend to dispatch) ---
                case "navigate":
                    page = params.get("page", "dashboard")
                    # Settings are modals/overlays, not page navigations
                    if page == "settings":
                        return {"action": "open_project_settings", "type": "client"}
                    if page == "admin":
                        return {"action": "open_system_settings", "type": "client"}
                    url_map = {
                        "dashboard": "/",
                        "debug": "/debug",
                    }
                    return {"action": "navigate", "url": url_map.get(page, f"/{page}"), "type": "client"}

                case "select_project":
                    project = fuzzy_match(params.get("project", ""), ctx.projects) or params.get("project", "")
                    # Look up collection for the matched project
                    collection_id = None
                    for pd in ctx.project_details:
                        if pd.name == project:
                            collection_id = pd.collection_id
                            break
                    result = {"action": "select_project", "project": project, "type": "client"}
                    if collection_id:
                        result["collection_id"] = collection_id
                    return result

                case "search_projects":
                    return {"action": "search_projects", "type": "client"}

                case "add_project":
                    return {"action": "add_project", "type": "client"}

                case "create_project":
                    name = params.get("name", "new-project")
                    description = params.get("description", "")
                    try:
                        import asyncio
                        from pathlib import Path as _P
                        from ..llm import analyze_project_description
                        from ..scaffold import create_project as scaffold_fn
                        from .db.models import Project
                        from .db.repositories import ProjectRepository

                        repo = ProjectRepository()
                        if repo.get(name):
                            return {"action": "create_project", "error": f"Project already exists: {name}", "success": False, "type": "server"}

                        # Use configured base path, falling back to ~/projects/
                        nb_cfg = load_nanobot_config()
                        base = nb_cfg.get("projects_base_path", "").strip()
                        base_dir = _P(base) if base else _P.home() / "projects"
                        base_dir.mkdir(parents=True, exist_ok=True)
                        project_path = base_dir / name

                        # Infer stack from description (runs LLM)
                        inferred = await asyncio.to_thread(analyze_project_description, description)
                        inferred["description"] = description

                        # Scaffold files
                        await asyncio.to_thread(scaffold_fn, path=project_path, name=name, config=inferred, register=False)

                        # Register in DB
                        tags = [inferred.get("type", "backend")] + inferred.get("features", [])
                        db_proj = Project(name=name, path=str(project_path), description=description, tags=[t for t in tags if t])
                        repo.upsert(db_proj)

                        # Spawn an agent to flesh out the scaffolded project
                        agent_info = None
                        try:
                            from .app import agent_manager as _amgr
                            if _amgr:
                                setup_task = (
                                    f"Set up the initial project structure and implement core functionality for: {name}\n\n"
                                    f"Description: {description}\n\n"
                                    f"The project has been scaffolded with a basic file structure. "
                                    f"Your job is to flesh out the actual code — implement the main features, "
                                    f"add proper error handling, and make the project functional."
                                )
                                agent_state = _amgr.spawn(project=name, task=setup_task)
                                agent_info = {"provider": agent_state.provider, "pid": agent_state.pid}
                                logger.info("Spawned setup agent for %s (pid=%s)", name, agent_state.pid)
                        except Exception as e:
                            logger.warning("Failed to spawn setup agent for %s: %s", name, e)

                        result = {"action": "create_project", "project": name, "success": True, "type": "server"}
                        if agent_info:
                            result["agent_spawned"] = True
                            result["agent"] = agent_info
                        return result
                    except Exception as e:
                        logger.error("create_project failed: %s", e, exc_info=True)
                        return {"action": "create_project", "error": str(e), "success": False, "type": "server"}

                case "select_collection":
                    collection = fuzzy_match(params.get("collection", ""), ctx.collections) or params.get("collection", "")
                    return {"action": "select_collection", "collection": collection, "type": "client"}

                case "show_tab":
                    return {"action": "show_tab", "tab": params.get("tab", "processes"), "type": "client"}

                case "show_logs":
                    process_query = params.get("process_id") or params.get("process")
                    if process_query and ctx.processes:
                        # Strategy 1: fuzzy match against process IDs and names
                        proc_id = fuzzy_match_process(process_query, ctx.processes)

                        # Strategy 2: if query looks like a project name, find first process for that project
                        if not proc_id:
                            project_match = fuzzy_match(process_query, [p.get("project", "") for p in ctx.processes if p.get("project")])
                            if project_match:
                                for p in ctx.processes:
                                    if p.get("project") == project_match:
                                        proc_id = p["id"]
                                        break

                        logger.info(
                            "show_logs: query=%r, candidates=%r, matched=%r",
                            process_query,
                            [(p["id"], p.get("project")) for p in ctx.processes],
                            proc_id,
                        )
                        if proc_id:
                            proc = next((p for p in ctx.processes if p["id"] == proc_id), None)
                            if proc:
                                return {
                                    "action": "show_process_logs",
                                    "process_id": proc["id"],
                                    "process_name": proc.get("name", proc["id"]),
                                    "type": "client",
                                }
                    return {"action": "show_logs", "type": "client"}

                case "show_activity":
                    return {"action": "show_activity", "type": "client"}

                case "show_screenshots":
                    return {"action": "show_screenshots", "type": "client"}

                case "open_terminal":
                    project = fuzzy_match(params.get("project", ""), ctx.projects) or params.get("project", "") or ctx.project
                    return {"action": "open_terminal", "project": project, "type": "client"}

                case "focus_terminal":
                    project = fuzzy_match(params.get("project", ""), ctx.projects) or params.get("project", "") or ctx.project
                    terminal_id = params.get("terminal_id", "")
                    if not terminal_id and project:
                        for t in ctx.terminals:
                            if t.get("project") == project and t.get("status") == "running":
                                terminal_id = t["id"]
                                break
                    return {"action": "focus_terminal", "project": project, "terminal_id": terminal_id, "type": "client"}

                case "open_browser":
                    url = params.get("url", "")
                    return {"action": "open_browser", "url": url, "type": "client"}

                case "focus_input":
                    target = params.get("target", "terminal")
                    return {"action": "focus_input", "target": target, "type": "client"}

                case "open_preview":
                    process_id = fuzzy_match_process(params.get("process_id", ""), ctx.processes) or params.get("process_id", "")
                    return {"action": "open_preview", "process_id": process_id, "type": "client"}

                # --- Server-side actions (executed here) ---
                case "start_process":
                    process_id = fuzzy_match_process(params.get("process_id", ""), ctx.processes) or params.get("process_id", "")
                    from .processes import get_process_manager
                    pm = get_process_manager()
                    result = pm.start(process_id)
                    success = result.status.value == "running" if hasattr(result, "status") else True
                    return {"action": "start_process", "process_id": process_id, "success": success, "type": "server"}

                case "stop_process":
                    process_id = fuzzy_match_process(params.get("process_id", ""), ctx.processes) or params.get("process_id", "")
                    from .processes import get_process_manager
                    pm = get_process_manager()
                    result = pm.stop(process_id)
                    success = result.status.value == "stopped" if hasattr(result, "status") else True
                    return {"action": "stop_process", "process_id": process_id, "success": success, "type": "server"}

                case "execute_action":
                    action_id = fuzzy_match_process(params.get("action_id", ""), ctx.processes) or params.get("action_id", "")
                    from .actions import get_action_manager
                    am = get_action_manager()
                    result = am.execute(action_id)
                    success = result.status.value in ("running", "completed") if hasattr(result, "status") else True
                    return {"action": "execute_action", "action_id": action_id, "success": success, "type": "server"}

                case "create_task":
                    project = params.get("project") or ctx.project
                    description = params.get("description", "")
                    from .db.repositories import get_task_repo, resolve_project_id
                    task_repo = get_task_repo()
                    project_id = resolve_project_id(project) if project else ""
                    task = task_repo.create(
                        project_id=project_id or "",
                        description=description,
                    )
                    return {
                        "action": "create_task",
                        "task_id": task.id,
                        "title": description[:60],
                        "project": project,
                        "success": True,
                        "type": "server",
                    }

                case "send_to_terminal":
                    text = params.get("text", "")
                    return {"action": "send_to_terminal", "text": text, "type": "client"}

                case "take_screenshot":
                    project = fuzzy_match(params.get("project", ""), ctx.projects) or params.get("project", "") or ctx.project
                    return {"action": "take_screenshot", "project": project, "type": "client"}

                case "spawn_agent":
                    project = fuzzy_match(params.get("project", ""), ctx.projects) or params.get("project", "") or ctx.project
                    task_desc = params.get("task", "")
                    # Create task + spawn via existing API
                    from .db.repositories import get_task_repo, resolve_project_id
                    task_repo = get_task_repo()
                    project_id = resolve_project_id(project) if project else ""
                    task = task_repo.create(
                        project_id=project_id or "",
                        description=task_desc,
                    )
                    return {
                        "action": "spawn_agent",
                        "project": project,
                        "task_id": task.id,
                        "task": task_desc,
                        "success": True,
                        "type": "server",
                    }

                case "end_phone_call":
                    return {"action": "end_phone_call", "type": "client"}

                case "pair_with_client":
                    from .state_machine import get_state_machine
                    from .channels.phone import get_phone_channel
                    sm = get_state_machine()
                    phone = get_phone_channel()
                    if not phone:
                        return {"action": "pair_with_client", "error": "Phone not available", "success": False, "type": "server"}
                    call = phone.get_active_call()
                    if not call:
                        return {"action": "pair_with_client", "error": "No active call", "success": False, "type": "server"}
                    clients = sm.get_connected_clients()
                    if not clients:
                        return {"action": "pair_with_client", "error": "No dashboard clients connected", "success": False, "type": "server"}
                    # Fuzzy match client name
                    client_name_query = params.get("client_name", "")
                    target = None
                    if client_name_query:
                        names = [c["client_name"] for c in clients]
                        matched = fuzzy_match(client_name_query, names)
                        if matched:
                            target = next(c for c in clients if c["client_name"] == matched)
                    if not target:
                        target = clients[0]  # Auto-select first
                    phone.pair(call.call_sid, target["client_id"])
                    # Notify client
                    await sm.send_to_client(target["client_id"], {
                        "type": "phone_paired",
                        "call_sid": call.call_sid,
                        "client_id": target["client_id"],
                    })
                    return {
                        "action": "pair_with_client",
                        "client_id": target["client_id"],
                        "client_name": target["client_name"],
                        "success": True,
                        "type": "server",
                    }

                case "unpair_client":
                    from .state_machine import get_state_machine
                    from .channels.phone import get_phone_channel
                    sm = get_state_machine()
                    phone = get_phone_channel()
                    if not phone:
                        return {"action": "unpair_client", "error": "Phone not available", "success": False, "type": "server"}
                    call = phone.get_active_call()
                    if not call:
                        return {"action": "unpair_client", "error": "No active call", "success": False, "type": "server"}
                    old_client = call.paired_client_id
                    phone.unpair(call.call_sid)
                    if old_client:
                        await sm.send_to_client(old_client, {
                            "type": "phone_unpaired",
                            "call_sid": call.call_sid,
                        })
                    return {"action": "unpair_client", "success": True, "type": "server"}

                case "enable_type_mode":
                    from .channels.phone import get_phone_channel
                    from .state_machine import get_state_machine
                    phone = get_phone_channel()
                    if not phone:
                        return {"action": "enable_type_mode", "error": "Phone not available", "success": False, "type": "server"}
                    call = phone.get_active_call()
                    if not call or not call.paired_client_id:
                        return {"action": "enable_type_mode", "error": "Must be paired first", "success": False, "type": "server"}
                    target = params.get("target", "terminal")
                    call.type_mode = True
                    call.type_mode_target = target
                    sm = get_state_machine()
                    await sm.send_to_client(call.paired_client_id, {
                        "type": "phone_type_mode",
                        "enabled": True,
                        "target": target,
                    })
                    return {"action": "enable_type_mode", "target": target, "success": True, "type": "server"}

                case "rename_client":
                    new_name = params.get("name", "").strip()
                    if not new_name:
                        return {"action": "rename_client", "error": "Name cannot be empty", "success": False, "type": "client"}
                    return {"action": "rename_client", "name": new_name, "success": True, "type": "client"}

                case "set_layout":
                    layout = params.get("layout", "desktop")
                    if layout not in ("desktop", "mobile", "kiosk"):
                        return {"action": "set_layout", "error": f"Unknown layout: {layout}", "success": False, "type": "client"}
                    return {"action": "set_layout", "layout": layout, "type": "client"}

                case "set_theme":
                    theme = params.get("theme", "default")
                    if theme not in ("default", "modern", "brutalist"):
                        return {"action": "set_theme", "error": f"Unknown theme: {theme}", "success": False, "type": "client"}
                    return {"action": "set_theme", "theme": theme, "type": "client"}

                case "restart_server":
                    import httpx
                    try:
                        from .config import get_rdc_home
                        from .db.repositories import get_event_repo
                        event_repo = get_event_repo()
                        event_repo.log("server.restart", message="Server reload triggered via voice/chat")
                        trigger_dir = get_rdc_home() / "reload-trigger"
                        trigger_dir.mkdir(exist_ok=True)
                        (trigger_dir / "restart.py").write_text(f"# {datetime.now().isoformat()}")
                        return {"action": "restart_server", "success": True, "type": "server"}
                    except Exception as e:
                        return {"action": "restart_server", "error": str(e), "success": False, "type": "server"}

                case "server_status":
                    import psutil
                    try:
                        proc = psutil.Process()
                        mem = proc.memory_info()
                        return {
                            "action": "server_status",
                            "uptime_seconds": int((datetime.now() - datetime.fromtimestamp(proc.create_time())).total_seconds()),
                            "memory_mb": round(mem.rss / 1024 / 1024, 1),
                            "cpu_percent": proc.cpu_percent(interval=0.1),
                            "pid": proc.pid,
                            "success": True,
                            "type": "server",
                        }
                    except Exception as e:
                        return {"action": "server_status", "error": str(e), "success": False, "type": "server"}

                case "kill_terminal":
                    terminal_id = params.get("terminal_id", "")
                    project = params.get("project", "") or ctx.project
                    if not terminal_id and project:
                        for t in ctx.terminals:
                            if t.get("project") == project and t.get("status") == "running":
                                terminal_id = t["id"]
                                break
                    if not terminal_id:
                        return {"action": "kill_terminal", "error": "No terminal found", "success": False, "type": "server"}
                    try:
                        from .terminal import get_terminal_manager
                        tm = get_terminal_manager()
                        tm.kill(terminal_id)
                        return {"action": "kill_terminal", "terminal_id": terminal_id, "success": True, "type": "server"}
                    except Exception as e:
                        return {"action": "kill_terminal", "error": str(e), "success": False, "type": "server"}

                case "restart_terminal":
                    terminal_id = params.get("terminal_id", "")
                    project = params.get("project", "") or ctx.project
                    if not terminal_id and project:
                        for t in ctx.terminals:
                            if t.get("project") == project:
                                terminal_id = t["id"]
                                break
                    if not terminal_id:
                        return {"action": "restart_terminal", "error": "No terminal found", "success": False, "type": "server"}
                    try:
                        from .terminal import get_terminal_manager
                        tm = get_terminal_manager()
                        session = tm.restart(terminal_id)
                        return {"action": "restart_terminal", "terminal_id": session.id, "success": True, "type": "server"}
                    except Exception as e:
                        return {"action": "restart_terminal", "error": str(e), "success": False, "type": "server"}

                case "toggle_sidebar":
                    return {"action": "toggle_sidebar", "type": "client"}

                case "toggle_chat":
                    return {"action": "toggle_chat", "type": "client"}

                case "restart_process":
                    process_id = fuzzy_match_process(params.get("process_id", ""), ctx.processes) or params.get("process_id", "")
                    from .processes import get_process_manager
                    pm = get_process_manager()
                    try:
                        result = pm.restart(process_id)
                        success = result.status.value == "running" if hasattr(result, "status") else True
                        return {"action": "restart_process", "process_id": process_id, "success": success, "type": "server"}
                    except Exception as e:
                        return {"action": "restart_process", "error": str(e), "success": False, "type": "server"}

                case "stop_all_processes":
                    project = params.get("project")
                    if project:
                        project = fuzzy_match(project, ctx.projects) or project
                    from .processes import get_process_manager
                    pm = get_process_manager()
                    stopped = []
                    for p in ctx.processes:
                        if p.get("status") != "running":
                            continue
                        if project and p.get("project") != project:
                            continue
                        try:
                            pm.stop(p["id"])
                            stopped.append(p["id"])
                        except Exception:
                            pass
                    return {"action": "stop_all_processes", "stopped": stopped, "count": len(stopped), "success": True, "type": "server"}

                case "start_all_processes":
                    project = params.get("project")
                    if project:
                        project = fuzzy_match(project, ctx.projects) or project
                    from .processes import get_process_manager
                    pm = get_process_manager()
                    started = []
                    for p in ctx.processes:
                        if p.get("status") == "running":
                            continue
                        if project and p.get("project") != project:
                            continue
                        try:
                            pm.start(p["id"])
                            started.append(p["id"])
                        except Exception:
                            pass
                    return {"action": "start_all_processes", "started": started, "count": len(started), "success": True, "type": "server"}

                case "disable_type_mode":
                    from .channels.phone import get_phone_channel
                    from .state_machine import get_state_machine
                    phone = get_phone_channel()
                    if not phone:
                        return {"action": "disable_type_mode", "error": "Phone not available", "success": False, "type": "server"}
                    call = phone.get_active_call()
                    if not call:
                        return {"action": "disable_type_mode", "error": "No active call", "success": False, "type": "server"}
                    call.type_mode = False
                    call.type_mode_target = None
                    if call.paired_client_id:
                        sm = get_state_machine()
                        await sm.send_to_client(call.paired_client_id, {
                            "type": "phone_type_mode",
                            "enabled": False,
                        })
                    return {"action": "disable_type_mode", "success": True, "type": "server"}

                # --- PinchTab browser automation ---
                case "browser_navigate":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_navigate", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_navigate", "error": "PinchTab not available", "success": False, "type": "server"}
                    if not await client.ensure_running():
                        return {"action": "browser_navigate", "error": "PinchTab failed to start", "success": False, "type": "server"}
                    url = params.get("url", "")
                    result = await client.navigate(url, tab_id=params.get("tab_id"))
                    return {"action": "browser_navigate", "url": url, "result": result, "success": True, "type": "server"}

                case "browser_snapshot":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_snapshot", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_snapshot", "error": "PinchTab not available", "success": False, "type": "server"}
                    if not await client.ensure_running():
                        return {"action": "browser_snapshot", "error": "PinchTab failed to start", "success": False, "type": "server"}
                    # Use filtered snapshot for reduced token cost
                    result = await client.snapshot_filtered(filter="interactive", compact=True, tab_id=params.get("tab_id"))
                    return {"action": "browser_snapshot", "snapshot": result, "success": True, "type": "server"}

                case "browser_click":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_click", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_click", "error": "PinchTab not available", "success": False, "type": "server"}
                    ref = params.get("ref", "e0")
                    # Take snapshot first so refs are valid
                    await client.snapshot(tab_id=params.get("tab_id"))
                    result = await client.action("click", ref, tab_id=params.get("tab_id"))
                    return {"action": "browser_click", "ref": ref, "result": result, "success": True, "type": "server"}

                case "browser_fill":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_fill", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_fill", "error": "PinchTab not available", "success": False, "type": "server"}
                    ref = params.get("ref", "e0")
                    value = params.get("value", "")
                    # Take snapshot first so refs are valid
                    await client.snapshot(tab_id=params.get("tab_id"))
                    # Use "type" action (simulates keystrokes) instead of "fill" (unreliable)
                    # First click to focus, then type
                    await client.action("click", ref, tab_id=params.get("tab_id"))
                    result = await client.action("type", ref, value=value, tab_id=params.get("tab_id"))
                    # Submit via JS Enter key dispatch (press action is buggy in PinchTab)
                    submit = params.get("submit", True)
                    if submit:
                        import asyncio as _asyncio
                        await _asyncio.sleep(0.5)
                        await client.evaluate(
                            "document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,bubbles:true}));"
                            "document.activeElement?.dispatchEvent(new KeyboardEvent('keypress',{key:'Enter',code:'Enter',keyCode:13,bubbles:true}));"
                            "document.activeElement?.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',keyCode:13,bubbles:true}));"
                            "document.activeElement?.form?.submit();",
                            tab_id=params.get("tab_id"),
                        )
                    return {"action": "browser_fill", "ref": ref, "result": result, "submitted": bool(submit), "success": True, "type": "server"}

                case "browser_text":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_text", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_text", "error": "PinchTab not available", "success": False, "type": "server"}
                    result = await client.text(tab_id=params.get("tab_id"))
                    return {"action": "browser_text", "text": result, "success": True, "type": "server"}

                case "browser_tabs":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_tabs", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_tabs", "error": "PinchTab not available", "success": False, "type": "server"}
                    result = await client.tabs()
                    return {"action": "browser_tabs", "tabs": result, "success": True, "type": "server"}

                case "browser_eval":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_eval", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_eval", "error": "PinchTab not available", "success": False, "type": "server"}
                    expression = params.get("expression", "")
                    result = await client.evaluate(expression, tab_id=params.get("tab_id"))
                    return {"action": "browser_eval", "result": result, "success": True, "type": "server"}

                case "browser_find":
                    from .pinchtab import get_pinchtab_client, load_pinchtab_config
                    if not load_pinchtab_config().get("enabled", True):
                        return {"action": "browser_find", "error": "PinchTab is disabled", "success": False, "type": "server"}
                    client = get_pinchtab_client()
                    if not client:
                        return {"action": "browser_find", "error": "PinchTab not available", "success": False, "type": "server"}
                    description = params.get("description", "")
                    result = await client.find(description, tab_id=params.get("tab_id"))
                    return {"action": "browser_find", "results": result, "success": True, "type": "server"}

                case _:
                    return {"action": action_name, "error": f"Unknown action: {action_name}", "success": False, "type": "server"}

        except Exception as e:
            return {"action": action_name, "error": str(e), "success": False, "type": "server"}


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_orchestrator_context(
    project: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: str = "desktop",
    client_id: Optional[str] = None,
) -> OrchestratorContext:
    """Build context from current system state."""
    projects: list[str] = []
    project_details: list[ProjectInfo] = []
    collections: list[str] = []
    collection_map: dict[str, str] = {}  # id -> name
    processes: list[dict] = []
    terminal_open = False
    active_collection = None

    try:
        from .db.repositories import get_collection_repo
        collection_repo = get_collection_repo()
        for c in collection_repo.list():
            collections.append(c.name)
            collection_map[c.id] = c.name
    except Exception:
        pass

    try:
        from .db.repositories import get_project_repo
        project_repo = get_project_repo()
        for p in project_repo.list():
            projects.append(p.name)
            col_name = collection_map.get(p.collection_id, "general")
            project_details.append(ProjectInfo(
                name=p.name,
                description=p.description,
                collection=col_name,
                collection_id=p.collection_id,
            ))
            # If the active project is known, resolve its collection
            if project and p.name == project:
                active_collection = col_name
    except Exception:
        pass

    # Load project profile from config
    project_profile: Optional[dict] = None
    if project and project != "all":
        try:
            from .db.repositories import get_project_repo as _get_proj_repo
            _proj = _get_proj_repo().get(project)
            if _proj and _proj.config and isinstance(_proj.config, dict):
                project_profile = _proj.config.get("profile")
        except Exception:
            pass

    try:
        from .processes import get_process_manager
        pm = get_process_manager()
        for p in pm.list():
            processes.append({
                "id": p.id,
                "name": p.name,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "port": p.port,
                "project": p.project,
            })
    except Exception:
        pass

    terminals: list[dict] = []
    try:
        from .terminal import get_terminal_manager
        tm = get_terminal_manager()
        for t in tm.list():
            terminals.append({
                "id": t.id,
                "project": t.project,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "command": t.command,
                "waiting_for_input": tm.is_waiting_for_input(t.id),
            })
        terminal_open = len(terminals) > 0
    except Exception:
        pass

    tasks: list[dict] = []
    try:
        from .db.repositories import get_task_repo
        task_repo = get_task_repo()
        for t in task_repo.list(limit=20):
            status = t.status.value if hasattr(t.status, "value") else str(t.status)
            tasks.append({
                "id": t.id,
                "project": getattr(t, "project", None) or getattr(t, "project_id", None),
                "title": getattr(t, "title", None),
                "description": (t.description or "")[:100],
                "status": status,
            })
    except Exception:
        pass

    agents: list[dict] = []
    try:
        from .state_machine import get_state_machine
        sm = get_state_machine()
        snapshot = sm.get_snapshot()
        for a in snapshot.agents:
            agent_dict = a if isinstance(a, dict) else a.model_dump() if hasattr(a, "model_dump") else {}
            if agent_dict:
                agents.append({
                    "project": agent_dict.get("project") or agent_dict.get("project_id", "?"),
                    "status": agent_dict.get("status", "unknown"),
                    "provider": agent_dict.get("provider", "unknown"),
                })
    except Exception:
        pass

    contexts: list[dict] = []
    try:
        from .browser import get_browser_manager
        bm = get_browser_manager()
        if bm:
            proj_filter = project if project and project != "all" else ""
            for c in bm.list_contexts(project_id=proj_filter, limit=10):
                contexts.append({
                    "id": c.id,
                    "url": c.url,
                    "title": c.title,
                    "timestamp": str(c.timestamp) if c.timestamp else "",
                })
    except Exception:
        pass

    # Connected clients and active call
    connected_clients: list[dict] = []
    active_call_sid: Optional[str] = None
    paired_client_id: Optional[str] = None
    paired_client_name: Optional[str] = None
    try:
        from .state_machine import get_state_machine
        sm = get_state_machine()
        connected_clients = sm.get_connected_clients()
    except Exception:
        pass
    if channel in ("phone", "phone_paired"):
        try:
            from .channels.phone import get_phone_channel
            phone = get_phone_channel()
            if phone:
                call = phone.get_active_call()
                if call:
                    active_call_sid = call.call_sid
                    if call.paired_client_id:
                        paired_client_id = call.paired_client_id
                        # Look up the client's human-readable name
                        for c in connected_clients:
                            if c.get("client_id") == paired_client_id:
                                paired_client_name = c.get("client_name") or paired_client_id
                                break
                        if not paired_client_name:
                            paired_client_name = paired_client_id
        except Exception:
            pass

    # PinchTab browser automation availability
    pinchtab_available = False
    pinchtab_tabs: list[dict] = []
    try:
        from .pinchtab import load_pinchtab_config, check_health
        pt_cfg = load_pinchtab_config()
        if pt_cfg.get("enabled", True):
            pinchtab_available = check_health(pt_cfg.get("port", 9867))
            if pinchtab_available:
                try:
                    from .pinchtab import get_pinchtab_client
                    pt = get_pinchtab_client()
                    if pt:
                        pinchtab_tabs = pt.tabs_sync()
                except Exception:
                    pass
    except Exception:
        pass

    return OrchestratorContext(
        project=project,
        collection=active_collection,
        projects=projects,
        project_details=project_details,
        collections=collections,
        processes=processes,
        tasks=tasks,
        terminals=terminals,
        agents=agents,
        contexts=contexts,
        terminal_open=terminal_open,
        channel=channel,
        connected_clients=connected_clients,
        active_call_sid=active_call_sid,
        client_id=client_id,
        paired_client_id=paired_client_id,
        paired_client_name=paired_client_name,
        project_profile=project_profile,
        pinchtab_available=pinchtab_available,
        pinchtab_tabs=pinchtab_tabs,
    )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: Optional[IntentEngine] = None
_executor: Optional[ActionExecutor] = None


def get_intent_engine() -> IntentEngine:
    global _engine
    if _engine is None:
        _engine = IntentEngine()
    return _engine


def get_action_executor() -> ActionExecutor:
    global _executor
    if _executor is None:
        _executor = ActionExecutor()
    return _executor
