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
    driver: str = "generic"  # "codex" | "claude" | "cursor" | "grok" | "generic" | "diagnostic" | "shell"
    icon: str = "•"
    description: str = ""
    enabled: bool = True
    builtin: bool = False
    source: str = "builtin"  # "builtin" | "alias" | "custom"

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
    if re.search(r"(?:^|\s|/)codex(?:\s|$)", exp_l):
        return "codex"
    if re.search(r"(?:^|\s|/)claude(?:\s|$)", exp_l):
        return "claude"
    if re.search(r"(?:^|\s|/)cursor-agent(?:\s|$)", exp_l):
        return "cursor"
    if re.search(r"(?:^|\s|/)grok(?:\s|$)", exp_l):
        return "grok"
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
    base_names = {"claude", "codex", "cursor", "cursor-agent", "grok"}
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


def _legacy_policy_path() -> Path:
    return get_rdc_home() / "agent_provider_policy.json"


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


def list_registered_wrappers(*, include_disabled: bool = False) -> list[AgentWrapperSpec]:
    """Return all registered wrappers (discovered aliases + custom wrappers + builtins)."""
    store = _load_registry_store()
    disabled_ids = set(store.get("disabled_ids") or [])
    custom_raw = store.get("custom_wrappers") or []
    custom_by_id: dict[str, AgentWrapperSpec] = {}
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
        "codex": ("Codex CLI", "codex", ["login", "status"], "O", "OPENAI_API_KEY"),
        "claude": ("Claude Code", "claude", ["auth", "status"], "A", "ANTHROPIC_API_KEY"),
        "cursor": ("Cursor Agent", "cursor-agent", ["about"], "C", "CURSOR_API_KEY"),
        "grok": ("Grok CLI", "grok", ["--version"], "G", "XAI_API_KEY"),
    }

    specs: list[AgentWrapperSpec] = []
    seen_ids: set[str] = set()

    # 1. Auto-discovered local shell aliases first
    aliases = discover_agent_aliases()
    for alias_id, info in sorted(aliases.items()):
        fam = info["family"]
        base_label, base_bin, auth_args, icon, env_key = family_meta.get(
            fam, (fam.title(), fam, ["--version"], "•", None)
        )
        setup_cmd = f"{alias_id} auth login" if fam == "claude" else f"{alias_id} login"
        resume_cmd = (
            f"{alias_id} --continue"
            if fam == "claude"
            else (f"{alias_id} --resume" if fam == "cursor" else f"{alias_id} resume --last")
        )
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

    # 3. User-added custom wrappers (e.g., grok, aider, goose, custom scripts)
    for wid, spec in custom_by_id.items():
        if wid in seen_ids:
            continue
        if wid in disabled_ids:
            spec.enabled = False
        if include_disabled or spec.enabled:
            specs.append(spec)
        seen_ids.add(wid)

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
    if spec.source not in {"builtin", "alias"}:
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
    """Remove a custom wrapper or disable a builtin/alias wrapper."""
    store = _load_registry_store()
    before = len(store.get("custom_wrappers") or [])
    store["custom_wrappers"] = [
        item for item in (store.get("custom_wrappers") or [])
        if isinstance(item, dict) and item.get("id") != wrapper_id
    ]
    after = len(store["custom_wrappers"])
    builtin_or_alias = wrapper_id in {b.id for b in _BUILTIN_SPECS} or wrapper_id in discover_agent_aliases()
    if builtin_or_alias:
        disabled = set(store.get("disabled_ids") or [])
        disabled.add(wrapper_id)
        store["disabled_ids"] = sorted(disabled)
        _save_registry_store(store)
        return True
    if after < before:
        _save_registry_store(store)
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
            executable={"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "grok": "grok"}.get(info["family"], info["family"]),
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
        base_bin = {"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "grok": "grok"}.get(family, family)
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
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, f'-p {prompt}')} > \"$rdc_final\""
        inner = _wrap_final_capture("grok", run_call)

    elif driver == "generic":
        base_exe = spec.executable or spec.terminal_command or provider
        run_call = f"{wrap_alias_invocation(alias_info, base_exe, prompt)} > \"$rdc_final\""
        inner = _wrap_final_capture("agent", run_call)

    else:
        raise ValueError(f"Unsupported session provider: {provider}")

    return f"trap 'echo __RDC_EXIT:$?' EXIT; {inner}"


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
        base_cmd = {"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "grok": "grok"}.get(driver, head)
    elif head in wrappers_by_id:
        spec = wrappers_by_id[head]
        driver = spec.driver
        base_cmd = spec.executable or spec.terminal_command or head
    elif head in ("cursor-agent", "claude", "codex", "grok"):
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
