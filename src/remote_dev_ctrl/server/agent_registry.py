"""Unified Agent Wrapper Registry & Driver Architecture.

Consolidates CLI agent wrappers (codex, claude, cursor, grok, custom wrappers,
and auto-discovered local shell aliases/functions like claude-work and codex-quill)
into a single declarative registry with first-class driver execution modes:
  1. `goal_run` — Non-interactive/supervised goal execution with structured final report capture
  2. `goal_resume` — Native provider session/thread continuation with durable fallback
  3. `interactive_terminal` — Interactive TUI launch & resume for fallback terminals
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from .config import get_rdc_home

logger = logging.getLogger(__name__)


class AgentWrapperSpec(BaseModel):
    id: str
    label: str
    driver: str = "generic"  # "codex" | "claude" | "cursor" | "grok" | "aider" | "opencode" | "goose" | "generic" | "diagnostic" | "shell"
    icon: str = "•"
    description: str = ""
    enabled: bool = True
    builtin: bool = False
    source: str = "builtin"  # "builtin" | "alias" | "custom" | "dropin"

    # Model Provider, Cognitive Tier & Hosting Metadata
    model_provider: str = "vendor"  # "openai" | "anthropic" | "cursor" | "xai" | "openrouter" | "ollama" | "vllm" | "mlx" | "vendor"
    default_model: str | None = None
    model_tier: str = "coding"  # "decision" | "fast" | "coding" | "reasoning"
    hosting: str = "cloud"  # "cloud" | "local" | "self_hosted" | "hybrid"
    api_base_url: str | None = None
    env_overrides: dict[str, str] = Field(default_factory=dict)

    # 1. Readiness & Auth Verification
    executable: str | None = None
    auth_check_args: list[str] = Field(default_factory=list)
    setup_command: str | None = None
    env_key: str | None = None

    # 2. Supervised Session Execution
    supports_sessions: bool = True
    session_command_template: str | None = None
    session_resume_template: str | None = None
    capabilities: list[str] = Field(default_factory=lambda: ["plan", "write", "verify"])

    # 3. Interactive Terminal Launch & Resume
    show_in_terminal_menu: bool = True
    terminal_command: str = ""
    terminal_resume_command: str | None = None


_BUILTIN_SPECS: list[AgentWrapperSpec] = [
    AgentWrapperSpec(
        id="codex",
        label="Codex CLI",
        driver="codex",
        icon="O",
        description="OpenAI Codex CLI",
        builtin=True,
        source="builtin",
        model_provider="openai",
        default_model="gpt-5.2-codex",
        model_tier="coding",
        hosting="cloud",
        executable="codex",
        auth_check_args=["login", "status"],
        setup_command="codex login",
        env_key="OPENAI_API_KEY",
        supports_sessions=True,
        show_in_terminal_menu=True,
        terminal_command="codex",
        terminal_resume_command="codex resume --last",
    ),
    AgentWrapperSpec(
        id="claude",
        label="Claude Code",
        driver="claude",
        icon="A",
        description="Anthropic Claude Code",
        builtin=True,
        source="builtin",
        model_provider="anthropic",
        default_model="claude-sonnet-4-5",
        model_tier="coding",
        hosting="cloud",
        executable="claude",
        auth_check_args=["auth", "status"],
        setup_command="claude auth login",
        env_key="ANTHROPIC_API_KEY",
        supports_sessions=True,
        show_in_terminal_menu=True,
        terminal_command="claude",
        terminal_resume_command="claude --continue",
    ),
    AgentWrapperSpec(
        id="cursor",
        label="Cursor Agent",
        driver="cursor",
        icon="C",
        description="Cursor AI Agent",
        builtin=True,
        source="builtin",
        model_provider="cursor",
        default_model="auto",
        model_tier="coding",
        hosting="cloud",
        executable="cursor-agent",
        auth_check_args=["about"],
        setup_command="cursor-agent login",
        env_key="CURSOR_API_KEY",
        supports_sessions=True,
        show_in_terminal_menu=True,
        terminal_command="cursor-agent",
        terminal_resume_command="cursor-agent --resume",
    ),
    AgentWrapperSpec(
        id="diagnostic",
        label="Diagnostic",
        driver="diagnostic",
        icon="D",
        description="No-op diagnostic verification runner",
        builtin=True,
        source="builtin",
        model_provider="local",
        default_model="deterministic",
        model_tier="decision",
        hosting="local",
        executable=None,
        supports_sessions=True,
        show_in_terminal_menu=False,
        capabilities=["verify"],
    ),
    AgentWrapperSpec(
        id="shell",
        label="Shell",
        driver="shell",
        icon="$",
        description="Plain login shell",
        builtin=True,
        source="builtin",
        model_provider="local",
        model_tier="fast",
        hosting="local",
        executable=None,
        supports_sessions=False,
        show_in_terminal_menu=True,
        terminal_command="",
        capabilities=[],
    ),
]


_ALIAS_LINE_RE = re.compile(r"^\s*alias\s+([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*$")
_FUNC_START_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z0-9_.-]+)\s*\(\s*\)\s*\{")
_SOURCE_LINE_RE = re.compile(r"^\s*(?:source|\.)\s+([^\s#;]+)")


def _strip_shell_quotes(raw: str) -> str:
    val = raw.strip()
    if len(val) >= 2 and ((val[0] == "'" and val[-1] == "'") or (val[0] == '"' and val[-1] == '"')):
        return val[1:-1].strip()
    val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
    if len(val) >= 2 and ((val[0] == "'" and val[-1] == "'") or (val[0] == '"' and val[-1] == '"')):
        return val[1:-1].strip()
    return val


def _candidate_shell_rc_files() -> list[Path]:
    home = Path.home()
    roots = [
        home / ".zshenv",
        home / ".zprofile",
        home / ".zshrc",
        home / ".zsh_aliases",
        home / ".aliases",
        home / ".bashrc",
        home / ".bash_profile",
    ]
    custom_dir = home / ".oh-my-zsh" / "custom"
    if custom_dir.is_dir():
        roots.extend(sorted(custom_dir.glob("*.zsh")))

    seen: set[Path] = set()
    ordered: list[Path] = []

    def _add(p: Path, follow_sources: bool = True) -> None:
        try:
            resolved = p.expanduser().resolve()
        except Exception:
            return
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        ordered.append(resolved)
        if not follow_sources:
            return
        try:
            for line in resolved.read_text(errors="replace").splitlines():
                m = _SOURCE_LINE_RE.match(line)
                if not m:
                    continue
                target = _strip_shell_quotes(m.group(1))
                target = target.replace("$HOME", str(home)).replace("${HOME}", str(home))
                if "$" in target:
                    continue
                _add(Path(target), follow_sources=False)
        except Exception:
            pass

    for r in roots:
        _add(r, follow_sources=True)
    return ordered


def classify_agent_family(name: str, expansion: str) -> str | None:
    name_l = name.lower()
    exp_l = expansion.lower()
    if name_l.startswith(("codex-", "codex_")) or re.search(r"\bcodex\b", name_l):
        return "codex"
    if name_l.startswith(("claude-", "claude_")) or re.search(r"\bclaude\b", name_l):
        return "claude"
    if name_l.startswith(("cursor-", "cursor_")) or re.search(r"\bcursor(?:-agent)?\b", name_l):
        return "cursor"
    if name_l.startswith(("grok-", "grok_")) or re.search(r"\bgrok\b", name_l):
        return "grok"
    if name_l.startswith(("agy-", "agy_")) or re.search(r"\bagy\b", name_l):
        return "agy"
    if name_l.startswith(("aider-", "aider_")) or re.search(r"\baider\b", name_l):
        return "aider"
    if name_l.startswith(("opencode-", "opencode_")) or re.search(r"\bopencode\b", name_l):
        return "opencode"
    if name_l.startswith(("goose-", "goose_")) or re.search(r"\bgoose\b", name_l):
        return "goose"
    if re.search(r"(?:^|\s|/)codex(?:\s|$)", exp_l):
        return "codex"
    if re.search(r"(?:^|\s|/)claude(?:\s|$)", exp_l):
        return "claude"
    if re.search(r"(?:^|\s|/)cursor-agent(?:\s|$)", exp_l):
        return "cursor"
    if re.search(r"(?:^|\s|/)grok(?:\s|$)", exp_l):
        return "grok"
    if re.search(r"(?:^|\s|/)agy(?:\s|$)", exp_l):
        return "agy"
    if re.search(r"(?:^|\s|/)aider(?:\s|$)", exp_l):
        return "aider"
    if re.search(r"(?:^|\s|/)opencode(?:\s|$)", exp_l):
        return "opencode"
    if re.search(r"(?:^|\s|/)goose(?:\s|$)", exp_l):
        return "goose"
    return None


def discover_agent_aliases() -> dict[str, dict[str, str]]:
    """Discover local shell aliases/functions for claude, codex, cursor-agent, and grok.

    Returns a mapping of alias_id -> {"id": alias_id, "family": family, "expansion": expansion, "kind": "alias"|"function"}.
    Never log or return `expansion` in API responses as it may contain inline env vars.
    """
    raw_aliases: dict[str, str] = {}
    raw_funcs: dict[str, str] = {}

    for rc_path in _candidate_shell_rc_files():
        try:
            lines = rc_path.read_text(errors="replace").splitlines()
        except Exception:
            continue
        i = 0
        while i < len(lines):
            line = lines[i]
            m_alias = _ALIAS_LINE_RE.match(line)
            if m_alias:
                alias_name, alias_val = m_alias.group(1), _strip_shell_quotes(m_alias.group(2))
                if alias_val:
                    raw_aliases[alias_name] = alias_val
                i += 1
                continue
            m_func = _FUNC_START_RE.match(line)
            if m_func:
                fname = m_func.group(1)
                func_lines = [line]
                brace_depth = line.count("{") - line.count("}")
                i += 1
                while i < len(lines) and brace_depth > 0:
                    func_lines.append(lines[i])
                    brace_depth += lines[i].count("{") - lines[i].count("}")
                    i += 1
                raw_funcs[fname] = "\n".join(func_lines)
                continue
            i += 1

    def _expand_alias(val: str) -> str:
        current = val
        for _ in range(3):
            parts = current.strip().split(None, 1)
            if not parts:
                break
            first = parts[0]
            if first in raw_aliases and raw_aliases[first] != current:
                rest = f" {parts[1]}" if len(parts) > 1 else ""
                current = f"{raw_aliases[first]}{rest}"
            else:
                break
        return current

    discovered: dict[str, dict[str, str]] = {}
    base_names = {"claude", "codex", "cursor", "cursor-agent", "grok", "agy"}
    for name, val in raw_aliases.items():
        if name in base_names:
            continue
        expanded = _expand_alias(val)
        expanded = re.sub(r"=~/", '="$HOME"/', expanded)
        family = classify_agent_family(name, expanded)
        if family:
            discovered[name] = {
                "id": name,
                "family": family,
                "expansion": expanded,
                "kind": "alias",
            }

    for fname, fbody in raw_funcs.items():
        if fname in base_names or fname in discovered:
            continue
        family = classify_agent_family(fname, fbody)
        if family:
            discovered[fname] = {
                "id": fname,
                "family": family,
                "expansion": fbody,
                "kind": "function",
            }

    return discovered


def _registry_path() -> Path:
    return get_rdc_home() / "agent_providers.json"


def _dropin_wrappers_dir() -> Path:
    return get_rdc_home() / "wrappers.d"


def _legacy_policy_path() -> Path:
    return get_rdc_home() / "agent_provider_policy.json"


def _load_dropin_wrappers() -> dict[str, AgentWrapperSpec]:
    """Load drop-in wrapper manifests from ~/.rdc/wrappers.d/*.json."""
    d = _dropin_wrappers_dir()
    if not d.is_dir():
        return {}
    found: dict[str, AgentWrapperSpec] = {}
    for p in sorted(d.glob("*.json")):
        try:
            raw = json.loads(p.read_text(errors="replace"))
            if not isinstance(raw, dict):
                continue
            wid = str(raw.get("id") or p.stem).strip()
            if not wid or wid.lower() == "gemini":
                continue
            raw["id"] = wid
            raw.setdefault("label", wid)
            raw.setdefault("source", "dropin")
            found[wid] = AgentWrapperSpec(**raw)
        except Exception:
            continue
    return found


def register_dropin_wrapper(spec: AgentWrapperSpec) -> Path:
    """Write a wrapper self-registration manifest to ~/.rdc/wrappers.d/<id>.json."""
    if spec.id.lower() == "gemini":
        raise ValueError("gemini CLI wrapper is deprecated and cannot be registered")
    d = _dropin_wrappers_dir()
    d.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", spec.id.strip())
    path = d / f"{safe_id}.json"
    payload = spec.model_dump()
    payload["source"] = "dropin"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _load_registry_store() -> dict[str, Any]:
    path = _registry_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass

    # Migrate from legacy policy file if present
    policy = {}
    legacy_policy = _legacy_policy_path()
    if legacy_policy.exists():
        try:
            raw = json.loads(legacy_policy.read_text())
            if isinstance(raw, dict):
                policy = raw
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "policy": policy,
        "custom_wrappers": [],
        "disabled_ids": [],
    }


def _save_registry_store(store: dict[str, Any]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2) + "\n")
    # Also keep legacy agent_provider_policy.json in sync for any external readers
    if "policy" in store and isinstance(store["policy"], dict):
        try:
            _legacy_policy_path().write_text(json.dumps(store["policy"], indent=2) + "\n")
        except OSError:
            pass


# Terminal starters and session agents are one catalog. A row in
# ~/.rdc/terminal_presets.json is an agent. These commands already know how
# to run a goal; every other command is a terminal until its row has a
# goal_command containing {prompt}.
_KNOWN_TERMINAL_AGENTS: dict[str, dict[str, Any]] = {
    "grok": {
        "label": "Grok CLI",
        "driver": "grok",
        "icon": "G",
        "executable": "grok",
        "description": "xAI Grok CLI",
        "model_provider": "xai",
        "hosting": "cloud",
        "auth_check_args": ["--version"],
        "setup_command": "grok login",
        "env_key": "XAI_API_KEY",
    },
    "agy": {
        "label": "agy",
        "driver": "agy",
        "icon": "Y",
        "executable": "agy",
        "description": "agy CLI",
        "model_provider": "vendor",
        "hosting": "cloud",
        "auth_check_args": ["--version"],
        "setup_command": "agy",
        "session_command_template": "agy --print --dangerously-skip-permissions {prompt}",
        "session_resume_template": "agy --print --dangerously-skip-permissions --continue {prompt}",
    },
}

_BUILTIN_COMMAND_IDS = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "cursor-agent": "cursor",
    "shell": "shell",
}

_DEFAULT_TERMINAL_PRESETS = [
    {"id": "codex", "label": "Codex", "command": "rdc-launch codex", "icon": "O", "description": "OpenAI Codex CLI", "goal_command": ""},
    {"id": "claude", "label": "Claude", "command": "rdc-launch claude --resume", "icon": "A", "description": "Anthropic Claude Code", "goal_command": ""},
    {"id": "cursor", "label": "Cursor", "command": "rdc-launch cursor-agent", "icon": "C", "description": "Cursor AI agent", "goal_command": ""},
    {"id": "shell", "label": "Shell", "command": "", "icon": "$", "description": "Plain login shell", "goal_command": ""},
]

_STARTER_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _terminal_presets_path() -> Path:
    return get_rdc_home() / "terminal_presets.json"


def command_head(command: str) -> str:
    """First executable token of a starter command. `rdc-launch claude --resume` is `claude`."""
    raw = (command or "").strip()
    if not raw:
        return ""
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    if not parts:
        return ""
    if parts[0] == "rdc-launch":
        parts = [part for part in parts[1:] if not part.startswith("-")]
    if not parts:
        return ""
    return Path(parts[0]).name


def starter_agent_id(row: dict[str, Any]) -> str | None:
    """Agent id a starter row addresses. Empty for a plain shell or an unsafe token."""
    head = command_head(str(row.get("command") or ""))
    if not head or head.lower() == "gemini":
        return None
    mapped = _BUILTIN_COMMAND_IDS.get(head)
    if mapped:
        return mapped
    if _STARTER_ID_RE.match(head):
        return head
    return None


def _starter_runs_goals(row: dict[str, Any], agent_id: str | None, aliases: dict[str, dict[str, str]]) -> bool:
    if not agent_id or agent_id == "shell":
        return False
    goal = str(row.get("goal_command") or "")
    if "{prompt}" in goal:
        return True
    if agent_id in _KNOWN_TERMINAL_AGENTS or agent_id in aliases:
        return True
    return agent_id in _BUILTIN_COMMAND_IDS.values() and agent_id != "shell"


def load_terminal_preset_rows() -> list[dict[str, Any]]:
    """Ordered + menu. Missing file uses the built-in starters."""
    path = _terminal_presets_path()
    rows: list[dict[str, Any]] | None = None
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, list):
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                command = str(item.get("command") or "")
                preset_id = str(item.get("id") or "")
                if preset_id.lower() == "gemini" or "gemini" in command.lower():
                    continue
                rows.append({
                    "id": preset_id,
                    "label": str(item.get("label") or ""),
                    "command": command,
                    "icon": str(item.get("icon") or "") or "•",
                    "description": str(item.get("description") or ""),
                    "goal_command": str(item.get("goal_command") or ""),
                })
            if len(rows) != len(data):
                _write_terminal_preset_rows(rows)
    if rows is None:
        rows = [dict(item) for item in _DEFAULT_TERMINAL_PRESETS]
    return rows


def _write_terminal_preset_rows(rows: list[dict[str, Any]]) -> None:
    path = _terminal_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n")


def _drop_starter_rows(agent_id: str) -> None:
    """Remove + menu rows that address this agent. Missing file stays missing."""
    if not _terminal_presets_path().exists():
        return
    rows = load_terminal_preset_rows()
    kept = [
        row for row in rows
        if starter_agent_id(row) != agent_id and str(row.get("id") or "") != agent_id
    ]
    if len(kept) != len(rows):
        _write_terminal_preset_rows(kept)


def _known_agent_spec(agent_id: str, row: dict[str, Any] | None = None) -> AgentWrapperSpec:
    meta = _KNOWN_TERMINAL_AGENTS[agent_id]
    source = row or {}
    command = str(source.get("command") or meta["executable"])
    goal = str(source.get("goal_command") or "").strip()
    return AgentWrapperSpec(
        id=agent_id,
        label=str(source.get("label") or meta["label"]),
        driver=str(meta["driver"]),
        icon=str(source.get("icon") or meta["icon"]),
        description=str(source.get("description") or meta["description"]),
        source="terminal",
        model_provider=str(meta.get("model_provider") or "vendor"),
        model_tier="coding",
        hosting=str(meta.get("hosting") or "cloud"),
        executable=str(meta["executable"]),
        auth_check_args=list(meta.get("auth_check_args") or []),
        setup_command=meta.get("setup_command"),
        env_key=meta.get("env_key"),
        supports_sessions=True,
        session_command_template=goal or meta.get("session_command_template"),
        session_resume_template=None if goal else meta.get("session_resume_template"),
        show_in_terminal_menu=True,
        terminal_command=command,
    )


def wrapper_for_starter(
    row: dict[str, Any],
    aliases: dict[str, dict[str, str]] | None = None,
) -> AgentWrapperSpec | None:
    """Build the agent a starter row adds. Builtins and plain aliases stay as they are."""
    agent_id = starter_agent_id(row)
    if not agent_id or agent_id in {spec.id for spec in _BUILTIN_SPECS}:
        return None
    aliases = aliases if aliases is not None else discover_agent_aliases()
    goal = str(row.get("goal_command") or "").strip()
    if agent_id in _KNOWN_TERMINAL_AGENTS:
        return _known_agent_spec(agent_id, row)
    if agent_id in aliases and not goal:
        return None
    supports = "{prompt}" in goal
    return AgentWrapperSpec(
        id=agent_id,
        label=str(row.get("label") or agent_id),
        driver="generic",
        icon=str(row.get("icon") or "") or "•",
        description=str(row.get("description") or f"Terminal starter {agent_id}"),
        source="terminal",
        executable=command_head(str(row.get("command") or "")) or agent_id,
        supports_sessions=supports,
        session_command_template=goal if supports else None,
        show_in_terminal_menu=True,
        terminal_command=str(row.get("command") or agent_id),
        auth_check_args=["--version"] if supports else [],
    )


def _path_known_agents(
    seen: set[str],
    disabled: set[str],
    which_fn: Callable[[str], str | None] = shutil.which,
) -> list[AgentWrapperSpec]:
    found: list[AgentWrapperSpec] = []
    for agent_id in _KNOWN_TERMINAL_AGENTS:
        if agent_id in seen or agent_id in disabled:
            continue
        if which_fn(_KNOWN_TERMINAL_AGENTS[agent_id]["executable"]) is None:
            continue
        found.append(_known_agent_spec(agent_id))
    return found


def _annotate_starter(row: dict[str, Any], aliases: dict[str, dict[str, str]]) -> dict[str, Any]:
    agent_id = starter_agent_id(row)
    goal = str(row.get("goal_command") or "")
    # Known agents and aliases run goals without a typed template. An override
    # is the only goal_command we echo back, so the field stays empty until set.
    explicit_goal = goal if "{prompt}" in goal else ""
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "command": str(row.get("command") or ""),
        "icon": str(row.get("icon") or "") or "•",
        "description": str(row.get("description") or ""),
        "goal_command": explicit_goal,
        "runs_goals": _starter_runs_goals(row, agent_id, aliases),
        "agent_id": agent_id or "",
    }


def terminal_menu() -> list[dict[str, Any]]:
    """Ordered starter list. Installed agents missing from the file are included."""
    aliases = discover_agent_aliases()
    rows = load_terminal_preset_rows()
    annotated = [_annotate_starter(row, aliases) for row in rows]
    existing_ids = {row["id"] for row in annotated}
    existing_cmds = {row["command"].strip() for row in annotated}
    existing_heads = {command_head(row["command"]) for row in annotated}
    extras: list[dict[str, Any]] = []
    for spec in list_registered_wrappers():
        if not spec.show_in_terminal_menu or spec.source == "builtin":
            continue
        command = (spec.terminal_command or spec.executable or spec.id).strip()
        head = command_head(command)
        if spec.id in existing_ids or command in existing_cmds or (head and head in existing_heads):
            continue
        extras.append({
            "id": spec.id,
            "label": spec.id if spec.source == "alias" else spec.label,
            "command": command,
            "icon": spec.icon or "•",
            "description": spec.description,
            "goal_command": "" if spec.id in _KNOWN_TERMINAL_AGENTS else (spec.session_command_template or ""),
            "runs_goals": spec.supports_sessions,
            "agent_id": spec.id,
        })
    return extras + annotated


def save_terminal_menu(presets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Save the + menu and make each new command an agent. Removed commands are disabled."""
    previous_ids = {
        agent_id
        for row in load_terminal_preset_rows()
        if (agent_id := starter_agent_id(row))
    }
    normalized: list[dict[str, Any]] = []
    for item in presets:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "")
        preset_id = str(item.get("id") or "")
        if preset_id.lower() == "gemini" or "gemini" in command.lower():
            continue
        normalized.append({
            "id": preset_id,
            "label": str(item.get("label") or ""),
            "command": command,
            "icon": str(item.get("icon") or "") or "•",
            "description": str(item.get("description") or ""),
            "goal_command": str(item.get("goal_command") or ""),
        })
    _write_terminal_preset_rows(normalized)

    aliases = discover_agent_aliases()
    builtin_ids = {spec.id for spec in _BUILTIN_SPECS}
    new_ids: set[str] = set()
    for row in normalized:
        spec = wrapper_for_starter(row, aliases)
        if spec is None:
            agent_id = starter_agent_id(row)
            if agent_id:
                new_ids.add(agent_id)
            continue
        new_ids.add(spec.id)
        upsert_wrapper_spec(spec)

    for removed in previous_ids - new_ids:
        if removed in builtin_ids or removed in aliases:
            continue
        remove_wrapper_spec(removed)
    return terminal_menu()


def list_registered_wrappers(*, include_disabled: bool = False) -> list[AgentWrapperSpec]:
    """Return all registered wrappers (discovered aliases + drop-in manifests + custom wrappers + builtins)."""
    store = _load_registry_store()
    disabled_ids = set(store.get("disabled_ids") or [])
    custom_raw = store.get("custom_wrappers") or []
    custom_by_id: dict[str, AgentWrapperSpec] = _load_dropin_wrappers()
    for item in custom_raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        wid = str(item["id"]).strip()
        if wid.lower() == "gemini":
            continue
        try:
            custom_by_id[wid] = AgentWrapperSpec(**item)
        except Exception:
            continue

    family_meta = {
        "codex": ("Codex CLI", "codex", ["login", "status"], "O", "OPENAI_API_KEY", "openai", "gpt-5.2-codex", "cloud"),
        "claude": ("Claude Code", "claude", ["auth", "status"], "A", "ANTHROPIC_API_KEY", "anthropic", "claude-sonnet-4-5", "cloud"),
        "cursor": ("Cursor Agent", "cursor-agent", ["about"], "C", "CURSOR_API_KEY", "cursor", "auto", "cloud"),
        "grok": ("Grok CLI", "grok", ["--version"], "G", "XAI_API_KEY", "xai", "grok-code-fast-1", "cloud"),
        "agy": ("agy", "agy", ["--version"], "Y", None, "vendor", None, "cloud"),
        "aider": ("Aider", "aider", ["--version"], "Ai", None, "ollama", "qwen3-coder", "local"),
        "opencode": ("OpenCode", "opencode", ["--version"], "OC", None, "openrouter", "qwen3-coder", "hybrid"),
        "goose": ("Goose Agent", "goose", ["--version"], "Gs", None, "ollama", "qwen3-coder", "local"),
    }

    specs: list[AgentWrapperSpec] = []
    seen_ids: set[str] = set()

    # 1. Auto-discovered local shell aliases first
    aliases = discover_agent_aliases()
    for alias_id, info in sorted(aliases.items()):
        fam = info["family"]
        base_label, base_bin, auth_args, icon, env_key, mod_prov, def_mod, hosting = family_meta.get(
            fam, (fam.title(), fam, ["--version"], "•", None, "vendor", None, "cloud")
        )
        if fam == "claude":
            setup_cmd = f"{alias_id} auth login"
            resume_cmd = f"{alias_id} --continue"
        elif fam == "cursor":
            setup_cmd = f"{alias_id} login"
            resume_cmd = f"{alias_id} --resume"
        elif fam == "grok":
            setup_cmd = f"{alias_id} login"
            resume_cmd = f"{alias_id} -c"
        elif fam == "agy":
            setup_cmd = alias_id
            resume_cmd = f"{alias_id} --continue"
        else:
            setup_cmd = f"{alias_id} login"
            resume_cmd = f"{alias_id} resume --last"
        if alias_id in custom_by_id:
            spec = custom_by_id[alias_id]
        else:
            spec = AgentWrapperSpec(
                id=alias_id,
                label=f"{alias_id} ({base_label})",
                driver=fam,
                icon=icon,
                description=f"Local {fam} shell {info.get('kind', 'alias')}",
                enabled=alias_id not in disabled_ids,
                builtin=False,
                source="alias",
                model_provider=mod_prov,
                default_model=def_mod,
                model_tier="coding",
                hosting=hosting,
                executable=base_bin,
                auth_check_args=auth_args,
                setup_command=setup_cmd,
                env_key=env_key,
                supports_sessions=True,
                show_in_terminal_menu=True,
                terminal_command=alias_id,
                terminal_resume_command=resume_cmd,
            )
        if include_disabled or spec.enabled:
            specs.append(spec)
        seen_ids.add(alias_id)

    # 2. Builtin specs (allowing custom overrides)
    for builtin in _BUILTIN_SPECS:
        if builtin.id in seen_ids:
            continue
        spec = custom_by_id.get(builtin.id, builtin.model_copy())
        if builtin.id in disabled_ids:
            spec.enabled = False
        if include_disabled or spec.enabled:
            specs.append(spec)
        seen_ids.add(builtin.id)

    # 3. User-added or drop-in self-registered wrappers (e.g., grok, aider, opencode, goose, custom scripts)
    for wid, spec in custom_by_id.items():
        if wid in seen_ids:
            continue
        if wid in disabled_ids:
            spec.enabled = False
        if include_disabled or spec.enabled:
            specs.append(spec)
        seen_ids.add(wid)

    # A starter row is an agent even before anyone opens the wrapper form.
    # Installed known CLIs (grok, agy) join the same way when no row names them.
    for row in load_terminal_preset_rows():
        spec = wrapper_for_starter(row, aliases)
        if spec is None or spec.id in seen_ids or spec.id in disabled_ids:
            continue
        if include_disabled or spec.enabled:
            specs.append(spec)
        seen_ids.add(spec.id)
    for spec in _path_known_agents(seen_ids, disabled_ids):
        if include_disabled or spec.enabled:
            specs.append(spec)
        seen_ids.add(spec.id)

    return specs


def upsert_wrapper_spec(spec: AgentWrapperSpec) -> AgentWrapperSpec:
    """Create or update a wrapper in ~/.rdc/agent_providers.json."""
    if spec.id.lower() == "gemini":
        raise ValueError("gemini CLI wrapper is deprecated and cannot be registered")
    store = _load_registry_store()
    custom_list = [
        item for item in (store.get("custom_wrappers") or [])
        if isinstance(item, dict) and item.get("id") != spec.id
    ]
    payload = spec.model_dump()
    if spec.source not in {"builtin", "alias", "dropin", "terminal"}:
        payload["source"] = "custom"
    custom_list.append(payload)
    store["custom_wrappers"] = custom_list
    disabled = set(store.get("disabled_ids") or [])
    if spec.enabled:
        disabled.discard(spec.id)
    else:
        disabled.add(spec.id)
    store["disabled_ids"] = sorted(disabled)
    _save_registry_store(store)
    return AgentWrapperSpec(**payload)


def remove_wrapper_spec(wrapper_id: str) -> bool:
    """Remove a custom/drop-in wrapper or disable a builtin/alias wrapper."""
    store = _load_registry_store()
    before = len(store.get("custom_wrappers") or [])
    store["custom_wrappers"] = [
        item for item in (store.get("custom_wrappers") or [])
        if isinstance(item, dict) and item.get("id") != wrapper_id
    ]
    after = len(store["custom_wrappers"])
    dropin_file = _dropin_wrappers_dir() / f"{wrapper_id}.json"
    removed_dropin = False
    if dropin_file.exists():
        try:
            dropin_file.unlink()
            removed_dropin = True
        except OSError:
            pass
    builtin_or_alias = wrapper_id in {b.id for b in _BUILTIN_SPECS} or wrapper_id in discover_agent_aliases()
    known = wrapper_id in _KNOWN_TERMINAL_AGENTS
    if builtin_or_alias or known:
        disabled = set(store.get("disabled_ids") or [])
        disabled.add(wrapper_id)
        store["disabled_ids"] = sorted(disabled)
        _save_registry_store(store)
        if known and not builtin_or_alias:
            _drop_starter_rows(wrapper_id)
        return True
    if after < before or removed_dropin:
        _save_registry_store(store)
        _drop_starter_rows(wrapper_id)
        return True
    return False


def load_agent_provider_policy() -> dict[str, str]:
    """Load the user's preferred provider policy, defaulting to a discovered codex alias or codex."""
    store = _load_registry_store()
    policy = store.get("policy") if isinstance(store.get("policy"), dict) else {}
    # Also check legacy policy path in case a test wrote directly to ~/.rdc/agent_provider_policy.json
    legacy_path = _legacy_policy_path()
    if legacy_path.exists():
        try:
            legacy_data = json.loads(legacy_path.read_text())
            if isinstance(legacy_data, dict) and legacy_data.get("preferred_provider"):
                policy = legacy_data
        except (OSError, json.JSONDecodeError):
            pass

    preferred = str(policy.get("preferred_provider") or "").strip()
    wrappers = list_registered_wrappers()
    valid_ids = {w.id for w in wrappers if w.supports_sessions and w.id != "diagnostic"}
    if preferred not in valid_ids:
        aliases = discover_agent_aliases()
        codex_aliases = sorted(k for k, v in aliases.items() if v.get("family") == "codex")
        preferred = codex_aliases[0] if codex_aliases else "codex"
    return {
        "preferred_provider": preferred,
        "fallback_policy": str(policy.get("fallback_policy") or "ask"),
    }


def save_agent_provider_policy(preferred_provider: str, fallback_policy: str = "ask") -> dict[str, str]:
    store = _load_registry_store()
    policy = {
        "preferred_provider": preferred_provider,
        "fallback_policy": fallback_policy,
    }
    store["policy"] = policy
    _save_registry_store(store)
    return policy


def resolve_provider_spec(provider: str) -> tuple[str, str, dict[str, str] | None, AgentWrapperSpec]:
    """Return (driver_family, invocation_prefix, alias_info, wrapper_spec) for a provider or alias."""
    if provider.lower() == "gemini":
        raise ValueError(f"Unsupported session provider: {provider}")

    aliases = discover_agent_aliases()
    wrappers_by_id = {w.id: w for w in list_registered_wrappers(include_disabled=True)}

    if provider in aliases:
        info = aliases[provider]
        spec = wrappers_by_id.get(provider) or AgentWrapperSpec(
            id=provider,
            label=provider,
            driver=info["family"],
            executable={"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "grok": "grok", "agy": "agy"}.get(info["family"], info["family"]),
            source="alias",
        )
        return spec.driver, info["expansion"], info, spec

    if provider in wrappers_by_id:
        spec = wrappers_by_id[provider]
        default_cmd = spec.terminal_command or spec.executable or provider
        return spec.driver, default_cmd, None, spec

    # Heuristic fallback if user typed a custom alias name like claude-work or codex-quill not yet in static parse
    family = classify_agent_family(provider, provider)
    if family:
        base_bin = {"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "grok": "grok", "agy": "agy"}.get(family, family)
        alias_info = {"id": provider, "family": family, "expansion": provider, "kind": "zsh_dynamic"}
        spec = AgentWrapperSpec(
            id=provider,
            label=f"{provider} ({family.title()})",
            driver=family,
            executable=base_bin,
            source="alias",
            terminal_command=provider,
        )
        return family, provider, alias_info, spec

    raise ValueError(f"Unsupported session provider: {provider}")


def wrap_alias_invocation(alias_info: dict[str, str] | None, default_cmd: str, args_str: str) -> str:
    if not alias_info:
        return f"{default_cmd} {args_str}".strip()
    kind = alias_info.get("kind")
    expansion = alias_info.get("expansion", default_cmd)
    alias_id = alias_info.get("id", default_cmd)
    if kind == "function":
        safe_id = re.sub(r"[^A-Za-z0-9_]", "_", alias_id)
        if safe_id != alias_id:
            # Shells that strictly adhere to POSIX identifier grammar (e.g. /bin/sh or bash in posix mode)
            # reject hyphens or dots in function names with "not a valid identifier".
            # Replace the declaration name with a POSIX-compliant identifier so it works across all shells.
            pattern = rf"(?m)^\s*(?:function\s+)?{re.escape(alias_id)}\s*(?:\(\s*\))?\s*(?:\r?\n)?\s*\{{"
            safe_expansion = re.sub(pattern, f"{safe_id}() {{", expansion, count=1)
            return f"{safe_expansion}\n{safe_id} {args_str}".strip()
        return f"{expansion}\n{alias_id} {args_str}".strip()
    if kind == "zsh_dynamic":
        inner = (
            "setopt aliases; "
            "[[ -f ~/.zshenv ]] && source ~/.zshenv >/dev/null 2>&1; "
            "[[ -f ~/.zshrc ]] && source ~/.zshrc >/dev/null 2>&1; "
            f"{alias_id} {args_str}"
        )
        return f"zsh -c {shlex.quote(inner)}"
    return f"{expansion} {args_str}".strip()


def resolve_codex_model_flag(invocation: str = "codex") -> str:
    """Return a `-m <model>` flag if config.toml specifies a retired model not in models_cache.json."""
    try:
        codex_dir = Path.home() / ".codex"
        m_home = re.search(r'CODEX_HOME=(?:["\']?)([^"\'\s]+)', invocation)
        if m_home:
            raw_dir = m_home.group(1).replace("$HOME", str(Path.home())).replace("~", str(Path.home()))
            custom_dir = Path(raw_dir).expanduser()
            if custom_dir.is_dir():
                codex_dir = custom_dir
        config_path = codex_dir / "config.toml"
        cache_path = codex_dir / "models_cache.json"
        if not cache_path.exists():
            cache_path = Path.home() / ".codex" / "models_cache.json"
        if not config_path.exists() or not cache_path.exists():
            return ""
        m = re.search(r'(?m)^model\s*=\s*"([^"]+)"', config_path.read_text(errors="replace"))
        if not m:
            return ""
        configured_model = m.group(1).strip()
        cache_data = json.loads(cache_path.read_text(errors="replace"))
        models = cache_data.get("models", []) if isinstance(cache_data, dict) else []
        slugs = [item.get("slug") for item in models if isinstance(item, dict) and item.get("slug")]
        if not slugs or configured_model in slugs:
            return ""
        return f" -m {shlex.quote(slugs[0])}"
    except Exception:
        return ""


def _wrap_final_capture(prefix: str, command_body: str) -> str:
    return (
        f"rdc_final=$(mktemp /tmp/rdc-{prefix}-final.XXXXXX); "
        f"{command_body}; "
        "rdc_code=$?; printf '\\n__RDC_FINAL_BEGIN__\\n'; cat \"$rdc_final\"; "
        "printf '\\n__RDC_FINAL_END__\\n'; rm -f \"$rdc_final\"; exit $rdc_code"
    )


def build_agent_command(
    provider: str,
    description: str,
    *,
    resume: bool = False,
    fallback_description: str | None = None,
) -> str:
    """Build the supervised session terminal command for a provider, alias, or custom wrapper.

    Supports both initial goal runs (`resume=False`) and native session resume (`resume=True`)
    with automatic fallback to `fallback_description` if no native session state exists.
    """
    prompt = shlex.quote(description[:4000])
    fallback_prompt = shlex.quote((fallback_description or description)[:4000])

    if provider == "shell":
        return f"trap 'echo __RDC_EXIT:$?' EXIT; {description}"
    if provider == "diagnostic":
        return "trap 'echo __RDC_EXIT:$?' EXIT; printf 'RDC diagnostic session completed without project writes\\n'"

    driver, invocation, alias_info, spec = resolve_provider_spec(provider)

    # Custom session template override
    if spec.session_command_template:
        if resume and spec.session_resume_template:
            templated = spec.session_resume_template.replace("{prompt}", prompt)
        else:
            templated = spec.session_command_template.replace("{prompt}", prompt)
        if "__RDC_FINAL_BEGIN__" not in templated:
            templated = _wrap_final_capture(spec.id, f"{templated} > \"$rdc_final\"")
        return f"trap 'echo __RDC_EXIT:$?' EXIT; {templated}"

    if driver == "codex":
        codex_model_flag = resolve_codex_model_flag(invocation)
        run_args = (
            f"--ask-for-approval never exec --sandbox workspace-write{codex_model_flag} "
            f"--output-last-message \"$rdc_final\" {fallback_prompt if resume else prompt}"
        )
        run_call = wrap_alias_invocation(alias_info, spec.executable or "codex", run_args)
        if resume:
            resume_args = (
                f"--ask-for-approval never exec resume --last{codex_model_flag} "
                f"--output-last-message \"$rdc_final\" {prompt}"
            )
            resume_call = wrap_alias_invocation(alias_info, spec.executable or "codex", resume_args)
            body = f"({resume_call}) || ({run_call})"
        else:
            body = run_call
        inner = _wrap_final_capture("codex", body)

    elif driver == "claude":
        perm_flag = "" if ("--dangerously-skip-permissions" in invocation or "--permission-mode" in invocation) else " --dangerously-skip-permissions"
        run_args = f"-p{perm_flag} {fallback_prompt if resume else prompt}".strip()
        run_call = f"{wrap_alias_invocation(alias_info, spec.executable or 'claude', run_args)} > \"$rdc_final\""
        if resume:
            resume_args = f"-p --continue{perm_flag} {prompt}".strip()
            resume_call = f"{wrap_alias_invocation(alias_info, spec.executable or 'claude', resume_args)} > \"$rdc_final\""
            body = f"({resume_call}) || ({run_call})"
        else:
            body = run_call
        inner = _wrap_final_capture("claude", body)

    elif driver == "cursor":
        run_args = f"-p --trust {fallback_prompt if resume else prompt}"
        run_call = f"{wrap_alias_invocation(alias_info, spec.executable or 'cursor-agent', run_args)} > \"$rdc_final\""
        if resume:
            resume_args = f"-p --trust --resume {prompt}"
            resume_call = f"{wrap_alias_invocation(alias_info, spec.executable or 'cursor-agent', resume_args)} > \"$rdc_final\""
            body = f"({resume_call}) || ({run_call})"
        else:
            body = run_call
        inner = _wrap_final_capture("cursor", body)

    elif driver == "grok":
        base_exe = spec.executable or "grok"
        resume_flag = "--continue " if resume else ""
        run_args = f"--permission-mode dontAsk {resume_flag}-p {prompt}"
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, run_args)} > \"$rdc_final\""
        inner = _wrap_final_capture("grok", run_call)

    elif driver == "agy":
        base_exe = spec.executable or "agy"
        resume_flag = "--continue " if resume else ""
        run_args = f"--print --dangerously-skip-permissions {resume_flag}{prompt}"
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, run_args)} > \"$rdc_final\""
        inner = _wrap_final_capture("agy", run_call)

    elif driver == "aider":
        base_exe = spec.executable or "aider"
        model_arg = f"--model {shlex.quote(spec.default_model)} " if spec.default_model else ""
        run_args = f"--yes-always {model_arg}--message {prompt}"
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, run_args)} > \"$rdc_final\""
        inner = _wrap_final_capture("aider", run_call)

    elif driver == "opencode":
        base_exe = spec.executable or "opencode"
        model_arg = f"-m {shlex.quote(spec.default_model)} " if spec.default_model else ""
        run_args = f"run {model_arg}{prompt}"
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, run_args)} > \"$rdc_final\""
        inner = _wrap_final_capture("opencode", run_call)

    elif driver == "goose":
        base_exe = spec.executable or "goose"
        run_args = f"run --text {prompt}"
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, run_args)} > \"$rdc_final\""
        inner = _wrap_final_capture("goose", run_call)

    elif driver == "generic":
        base_exe = spec.executable or spec.terminal_command or provider
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, prompt)} > \"$rdc_final\""
        inner = _wrap_final_capture("agent", run_call)

    else:
        raise ValueError(f"Unsupported session provider: {provider}")

    # Inject non-secret environment overrides (e.g., OPENAI_BASE_URL, ANTHROPIC_BASE_URL, OLLAMA_HOST)
    env_parts: list[str] = []
    if spec.api_base_url:
        if driver == "claude":
            env_parts.append(f"ANTHROPIC_BASE_URL={shlex.quote(spec.api_base_url)}")
        else:
            env_parts.append(f"OPENAI_BASE_URL={shlex.quote(spec.api_base_url)}")
    for k, v in (spec.env_overrides or {}).items():
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(k)):
            env_parts.append(f"{k}={shlex.quote(str(v))}")
    env_prefix = f"export {' '.join(env_parts)}; " if env_parts else ""

    return f"trap 'echo __RDC_EXIT:$?' EXIT; {env_prefix}{inner}"


def check_provider_blocker(
    provider: str,
    *,
    which_fn: Callable[[str], str | None] = shutil.which,
    run_fn: Callable[..., Any] = subprocess.run,
) -> str | None:
    """Return a user-facing blocker before creating a terminal for a provider or alias."""
    if provider == "diagnostic":
        return None
    try:
        driver, _invocation, alias_info, spec = resolve_provider_spec(provider)
    except ValueError:
        return f"Unsupported session provider: {provider}"

    default_checks = {
        "cursor": ("cursor-agent", ["about"], "Cursor Agent", "cursor-agent login", "CURSOR_API_KEY"),
        "codex": ("codex", ["login", "status"], "Codex CLI", "codex login", "OPENAI_API_KEY"),
        "claude": ("claude", ["auth", "status"], "Claude Code", "claude auth login", "ANTHROPIC_API_KEY"),
        "grok": ("grok", ["--version"], "Grok CLI", "grok login", "XAI_API_KEY"),
        "agy": ("agy", ["--version"], "agy", "agy", ""),
    }
    if driver in default_checks:
        base_bin, sub_args, base_label, setup_command, key_name = default_checks[driver]
    else:
        base_bin = spec.executable or provider
        sub_args = list(spec.auth_check_args)
        base_label = spec.label or provider
        setup_command = spec.setup_command or f"{provider} login"
        key_name = spec.env_key or "API_KEY"

    label = f"{provider} ({base_label})" if alias_info else (spec.label or base_label)
    if alias_info:
        setup_command = f"{provider} {' '.join(sub_args[:-1])} login".strip() if driver == "claude" else f"{provider} login"

    if base_bin and not which_fn(base_bin):
        return f"{label} is not installed on the RDC host."

    if not sub_args:
        if key_name and os.environ.get(key_name):
            return None
        return None

    try:
        if alias_info:
            shell_cmd = wrap_alias_invocation(alias_info, base_bin, " ".join(sub_args))
            result = run_fn(
                ["zsh", "-c", shell_cmd],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        else:
            result = run_fn(
                [base_bin, *sub_args],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
    except FileNotFoundError:
        return f"{label} is not installed on the RDC host."
    except subprocess.TimeoutExpired:
        return f"{label} authentication check timed out. Run `{setup_command}` on the RDC host."

    stdout_lower = (getattr(result, "stdout", "") or "").lower()
    if result.returncode != 0 or (driver == "cursor" and "not logged in" in stdout_lower):
        if key_name and os.environ.get(key_name):
            return None
        return f"{label} is not authenticated for RDC. Run `{setup_command}` on the RDC host or configure {key_name}."
    return None


def list_provider_readiness(
    *,
    which_fn: Callable[[str], str | None] = shutil.which,
    blocker_fn: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Probe all enabled session-capable wrappers in parallel."""
    check_fn = blocker_fn or (lambda pid: check_provider_blocker(pid, which_fn=which_fn))
    specs = [w for w in list_registered_wrappers() if w.supports_sessions]

    def _probe(spec: AgentWrapperSpec) -> dict[str, Any]:
        installed = spec.executable is None or which_fn(spec.executable) is not None
        blocker = check_fn(spec.id) if installed else f"{spec.label} is not installed on the RDC host."
        return {
            "id": spec.id,
            "label": spec.label,
            "driver": spec.driver,
            "source": spec.source,
            "icon": spec.icon,
            "description": spec.description,
            "model_provider": spec.model_provider,
            "default_model": spec.default_model,
            "model_tier": spec.model_tier,
            "hosting": spec.hosting,
            "api_base_url": spec.api_base_url,
            "installed": installed,
            "ready": blocker is None,
            "status": "ready" if blocker is None else ("needs_auth" if installed else "not_installed"),
            "detail": blocker,
            "setup_command": spec.setup_command,
            "terminal_command": spec.terminal_command,
            "terminal_resume_command": spec.terminal_resume_command,
            "capabilities": spec.capabilities,
        }

    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        return list(pool.map(_probe, specs))


def resolve_interactive_terminal_command(
    command: str,
    cwd: str,
    project: str,
    mode: str | None = "new",
) -> str:
    """Resolve an interactive terminal command using the unified wrapper registry.

    Handles `rdc-launch <agent> [--resume]`, wrapper IDs (`claude-work`, `codex-quill`),
    session resume flags (`mode="continue"` or `mode="resume:<id>"`), and shell alias expansion.
    """
    raw = (command or "").strip()
    if not raw or raw.startswith("trap ") or ";" in raw or "\n" in raw:
        return raw

    # Normalize `rdc-launch <agent> [--resume]` through the registry when possible
    want_resume = mode == "continue"
    resume_session_id: str | None = None
    if mode and mode.startswith("resume:"):
        resume_session_id = mode.split(":", 1)[1].strip()

    stripped = raw
    if stripped.startswith("rdc-launch "):
        tokens = stripped.split()[1:]
        if "--resume" in tokens or "--continue" in tokens:
            want_resume = True
            tokens = [t for t in tokens if t not in ("--resume", "--continue")]
        if tokens:
            stripped = " ".join(tokens)

    parts = stripped.split(None, 1)
    head = parts[0] if parts else ""
    tail = parts[1] if len(parts) > 1 else ""

    aliases = discover_agent_aliases()
    wrappers_by_id = {w.id: w for w in list_registered_wrappers(include_disabled=True)}

    driver: str | None = None
    alias_info: dict[str, str] | None = None
    base_cmd = head

    if head in aliases:
        alias_info = aliases[head]
        driver = alias_info["family"]
        base_cmd = {"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "grok": "grok", "agy": "agy"}.get(driver, head)
    elif head in wrappers_by_id:
        spec = wrappers_by_id[head]
        driver = spec.driver
        base_cmd = spec.executable or spec.terminal_command or head
    elif head in ("cursor-agent", "claude", "codex", "grok", "agy"):
        driver = "cursor" if head == "cursor-agent" else head

    # Determine resume arguments if not already present in tail
    if driver and "--resume" not in tail and "--continue" not in tail and "resume" not in tail.split():
        if resume_session_id and driver in ("cursor", "claude"):
            tail = f"{tail} --resume={resume_session_id}".strip()
        elif driver == "cursor":
            # Check recorded cursor session ID in DB or .cursor directory in cwd
            attached = False
            try:
                from .db.repositories import get_agent_session_repo, resolve_project_id

                project_id = resolve_project_id(project)
                if project_id:
                    latest = get_agent_session_repo().get_latest(project_id)
                    if latest and latest.agent_session_id:
                        tail = f"{tail} --resume={latest.agent_session_id}".strip()
                        attached = True
            except Exception:
                pass
            if not attached and want_resume:
                cursor_dir = Path(cwd) / ".cursor"
                if cursor_dir.is_dir() and any(cursor_dir.iterdir()):
                    tail = f"{tail} --resume".strip()
        elif driver == "claude" and want_resume:
            # Check .claude in cwd or global ~/.claude/projects
            claude_dir = Path(cwd) / ".claude"
            has_local = claude_dir.is_dir() and any(claude_dir.rglob("*.jsonl"))
            if has_local or (Path.home() / ".claude" / "projects").is_dir():
                tail = f"{tail} --continue".strip()

    if alias_info:
        return wrap_alias_invocation(alias_info, base_cmd, tail)

    # If the user was invoking `rdc-launch` with no alias, rebuild clean command
    if raw.startswith("rdc-launch ") and driver:
        return f"{base_cmd} {tail}".strip()

    return f"{head} {tail}".strip()


# ---------------------------------------------------------------------------
# Model Providers, Open-Source / Self-Hosted Discovery & Tier Matrix
# ---------------------------------------------------------------------------

def infer_model_tier(model_name: str) -> str:
    """Infer the appropriate cognitive tier ('decision', 'fast', 'coding', 'reasoning') for a model."""
    m = model_name.lower()
    if any(k in m for k in ("jev", "laya", "openjev", "guard", "classifier")):
        return "decision"
    if any(k in m for k in ("r1", "qwq", "opus", "o1", "o3", "reason", "thinking", "pro")):
        return "reasoning"
    if any(k in m for k in ("coder", "codex", "sonnet", "deepseek-v3", "devstral", "starcoder", "codellama")):
        return "coding"
    return "fast"


def discover_local_ollama_models(run_fn: Callable[..., Any] = subprocess.run) -> list[dict[str, Any]]:
    """Return locally pulled open-source models from Ollama (`ollama list`)."""
    if not shutil.which("ollama"):
        return []
    try:
        res = run_fn(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        if res.returncode != 0:
            return []
        lines = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
        models: list[dict[str, Any]] = []
        for line in lines[1:]:
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            models.append({
                "id": f"ollama/{name}",
                "name": name,
                "provider": "ollama",
                "hosting": "local",
                "tier": infer_model_tier(name),
            })
        return models
    except Exception:
        return []


def discover_model_providers() -> list[dict[str, Any]]:
    """Return cloud, open-source local, and self-hosted inference providers & their readiness."""
    from .vault import get_secret

    ollama_installed = shutil.which("ollama") is not None
    ollama_models = discover_local_ollama_models() if ollama_installed else []
    store = _load_registry_store()
    custom_endpoints = store.get("model_endpoints") or []

    has_openrouter = bool(get_secret("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))
    has_openai = bool(get_secret("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    has_anthropic = bool(get_secret("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    has_xai = bool(get_secret("XAI_API_KEY") or os.environ.get("XAI_API_KEY"))
    decision_url = os.environ.get("RDC_DECISION_API_URL", "").strip()

    providers: list[dict[str, Any]] = [
        {
            "id": "ollama",
            "label": "Ollama (Local Open-Source)",
            "hosting": "local",
            "base_url": "http://localhost:11434/v1",
            "ready": ollama_installed,
            "models": ollama_models,
            "recommended_models": ["qwen3-coder:32b", "qwen3.5:8b", "deepseek-r1:32b", "gemma4"],
            "description": "Zero-cost, private local open-weight models on HomeBase.",
        },
        {
            "id": "self_hosted",
            "label": "Self-Hosted vLLM / MLX / Laya",
            "hosting": "self_hosted",
            "base_url": decision_url or (custom_endpoints[0].get("base_url") if custom_endpoints else "http://localhost:8000/v1"),
            "ready": bool(decision_url or custom_endpoints),
            "models": custom_endpoints,
            "recommended_models": ["OpenJev-9B", "convaiinnovations/laya", "Qwen/Qwen3-Coder-32B-Instruct", "deepseek-ai/DeepSeek-R1"],
            "description": "OpenAI-compatible or Jev-compatible inference servers on LAN / Tailscale / Apple Silicon MLX.",
        },
        {
            "id": "typesafe_jev",
            "label": "TypeSafe Jev / Decision API",
            "hosting": "hybrid",
            "base_url": decision_url or "https://openrouter.ai/api/alpha/decisions",
            "ready": True,  # Always ready via calibrated local System 1 + optional remote Jev
            "models": [{"id": "typesafe/jev-1.14", "tier": "decision"}, {"id": "rdc-jev-calibrated-v1", "tier": "decision"}],
            "recommended_models": ["typesafe/jev-1.14", "openjev-latest", "rdc-jev-calibrated-v1"],
            "description": "System 1 structured probabilistic decisions (Choice, Score, Noul) in <50ms.",
        },
        {
            "id": "openrouter",
            "label": "OpenRouter (Multi-Provider Cloud)",
            "hosting": "cloud",
            "base_url": "https://openrouter.ai/api/v1",
            "ready": has_openrouter,
            "models": [],
            "recommended_models": ["anthropic/claude-sonnet-4", "google/gemini-2.5-flash", "deepseek/deepseek-chat-v3", "typesafe/jev-1.14"],
            "description": "Unified gateway to frontier cloud and open-weight models.",
        },
        {
            "id": "openai",
            "label": "OpenAI",
            "hosting": "cloud",
            "base_url": "https://api.openai.com/v1",
            "ready": has_openai or shutil.which("codex") is not None,
            "models": [],
            "recommended_models": ["gpt-5.2-codex", "gpt-5.4", "gpt-4o-mini"],
            "description": "Powers Codex CLI and direct OpenAI reasoning/coding models.",
        },
        {
            "id": "anthropic",
            "label": "Anthropic",
            "hosting": "cloud",
            "base_url": "https://api.anthropic.com/v1",
            "ready": has_anthropic or shutil.which("claude") is not None,
            "models": [],
            "recommended_models": ["claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5"],
            "description": "Powers Claude Code and high-trust multi-file engineering.",
        },
        {
            "id": "xai",
            "label": "xAI (Grok)",
            "hosting": "cloud",
            "base_url": "https://api.x.ai/v1",
            "ready": has_xai or shutil.which("grok") is not None,
            "models": [],
            "recommended_models": ["grok-code-fast-1", "grok-4"],
            "description": "Powers Grok CLI and fast code synthesis.",
        },
    ]
    return providers


def get_model_tier_matrix() -> list[dict[str, Any]]:
    """Return the 4-tier cognitive routing matrix mapping task types to cloud & open-source models."""
    return [
        {
            "tier": "decision",
            "label": "Tier 0 · System 1 Decision Engine",
            "purpose": "Sub-50ms typed probabilistic decisions (Choice, Score, Noul): command risk gating, session blocker triage, intent routing, memory worthiness.",
            "cloud_models": ["typesafe/jev-1.14 (OpenRouter Decisions API)"],
            "open_source_models": ["OpenJev-9B", "convaiinnovations/laya (ONNX/MLX)", "rdc-jev-calibrated-v1 (Built-in)"],
        },
        {
            "tier": "fast",
            "label": "Tier 1 · Fast Utility & Chief-of-Staff Chat",
            "purpose": "Low-latency workstream briefs, quick Q&A, knowledge extraction, and command translation.",
            "cloud_models": ["claude-haiku-4-5", "gemini-2.5-flash", "gpt-4o-mini"],
            "open_source_models": ["ollama/qwen3.5:8b", "ollama/gemma4", "ollama/llama3.2:3b"],
        },
        {
            "tier": "coding",
            "label": "Tier 2 · Supervised Coding & Implementation",
            "purpose": "Multi-file feature implementation, test writing, bug fixing, and refactoring in supervised sessions.",
            "cloud_models": ["gpt-5.2-codex (Codex CLI)", "claude-sonnet-4-5 (Claude Code)", "grok-code-fast-1"],
            "open_source_models": ["ollama/qwen3-coder:32b (via Aider / Codex --oss / OpenCode)", "deepseek-v3", "devstral-small"],
        },
        {
            "tier": "reasoning",
            "label": "Tier 3 · Deep Architecture & Root-Cause Review",
            "purpose": "Complex system architecture, subtle race-condition debugging, security threat modeling, and ambiguity resolution.",
            "cloud_models": ["claude-opus-4-5", "gpt-5.4-pro / o3"],
            "open_source_models": ["ollama/deepseek-r1:32b", "ollama/qwq:32b"],
        },
    ]


def get_wrapper_self_registration_instructions(server_url: str = "http://127.0.0.1:8420") -> dict[str, Any]:
    """Return self-registration instructions, schema, CLI command, drop-in path, and copyable agent prompt."""
    dropin_dir = str(_dropin_wrappers_dir())
    example_manifest = {
        "id": "aider-qwen-local",
        "label": "Aider (Local Qwen3-Coder)",
        "driver": "aider",
        "icon": "Ai",
        "description": "Self-hosted open-source coding agent via local Ollama",
        "model_provider": "ollama",
        "default_model": "ollama_chat/qwen3-coder:32b",
        "model_tier": "coding",
        "hosting": "local",
        "api_base_url": "http://localhost:11434/v1",
        "executable": "aider",
        "auth_check_args": ["--version"],
        "setup_command": "ollama pull qwen3-coder:32b",
        "supports_sessions": True,
        "show_in_terminal_menu": True,
        "terminal_command": "aider --model ollama_chat/qwen3-coder:32b",
        "session_command_template": "aider --yes-always --model ollama_chat/qwen3-coder:32b --message {prompt}",
    }

    agent_prompt = (
        "Register yourself (or the requested model wrapper) as an available agent provider in Remote Dev Control (RDC).\n"
        "You can self-register using ANY of these 3 methods:\n\n"
        "Method 1 (MCP Tool — if connected to `remote-dev-ctrl` MCP server):\n"
        "  Call the `register_agent_wrapper` MCP tool with your wrapper metadata (id, label, driver, model_provider, default_model, model_tier, hosting, executable, terminal_command, session_command_template).\n\n"
        f"Method 2 (Drop-in JSON Manifest — zero dependencies, works offline):\n"
        f"  Write a JSON file to `{dropin_dir}/<your-wrapper-id>.json` matching this schema:\n"
        f"  {json.dumps(example_manifest, indent=2)}\n\n"
        "Method 3 (RDC CLI):\n"
        "  Run: `rdc wrapper register <id> --label \"<Label>\" --driver <codex|claude|cursor|grok|aider|opencode|goose|generic> "
        "--model-provider <openai|anthropic|openrouter|ollama|vllm> --model <model-name> --hosting <cloud|local|self_hosted> "
        "--executable <binary> --terminal-cmd \"<cmd>\" --session-cmd \"<cmd with {prompt}>\"`"
    )

    return {
        "dropin_directory": dropin_dir,
        "registry_file": str(_registry_path()),
        "mcp_tool": "register_agent_wrapper",
        "cli_command": (
            "rdc wrapper register <id> --label '<Label>' --driver <driver> "
            "--model-provider <provider> --model <model> --hosting <cloud|local|self_hosted>"
        ),
        "http_endpoint": f"PUT {server_url}/settings/agent-providers/wrappers/{{wrapper_id}}",
        "example_manifest": example_manifest,
        "agent_prompt": agent_prompt,
    }

