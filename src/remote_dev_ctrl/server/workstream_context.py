"""Workstream context assembler — builds structured, token-budgeted context for LLM agents.

Architecture: layered context with priority-based compaction.

    Identity  (~200 tok)  — project name, stack, conventions (from profile/rules)
    State     (~400 tok)  — running terminals, actions, tasks, git branch
    Recent    (~1500 tok) — last N channel messages + structured events (full detail)
    Summary   (~1000 tok) — compacted older conversation history
    Git       (~400 tok)  — recent commits, dirty files

Each layer has a token budget. If a layer overflows, it truncates from the
least-important end. Recent detail always wins over older summary.

Token estimation: ~4 chars per token (rough English approximation).
"""

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .utils import (
    strip_ansi, enum_value,
    get_project_repo, get_terminal_manager, get_process_manager,
    get_task_repo, get_channel_manager, get_conversation_manager,
)

logger = logging.getLogger(__name__)

# Rough token estimation: 1 token ≈ 4 chars for English text
CHARS_PER_TOKEN = 4


@dataclass
class ContextLayer:
    """A single layer of assembled context."""
    name: str
    content: str
    priority: int  # higher = kept when over budget
    token_budget: int
    estimated_tokens: int = 0

    def __post_init__(self):
        self.estimated_tokens = len(self.content) // CHARS_PER_TOKEN


@dataclass
class WorkstreamContext:
    """Fully assembled workstream context."""
    channel_id: str
    channel_name: str
    project: Optional[str]
    layers: list[ContextLayer] = field(default_factory=list)
    total_tokens: int = 0
    truncated: bool = False

    def to_prompt(self) -> str:
        """Format as a single string for LLM system prompt injection."""
        parts = []
        for layer in sorted(self.layers, key=lambda l: l.priority, reverse=True):
            if layer.content.strip():
                parts.append(f"## {layer.name}\n{layer.content}")
        return "\n\n".join(parts)

    def to_sections(self) -> dict[str, str]:
        """Return as a dict of section name → content."""
        return {layer.name: layer.content for layer in self.layers if layer.content.strip()}


@dataclass
class ContextBudget:
    """Token budget allocation per layer."""
    identity: int = 200
    state: int = 400
    recent: int = 1500
    summary: int = 1000
    git: int = 400

    @property
    def total(self) -> int:
        return self.identity + self.state + self.recent + self.summary + self.git


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _truncate_to_budget(text: str, token_budget: int) -> tuple[str, bool]:
    """Truncate text to fit within token budget. Returns (text, was_truncated)."""
    char_budget = token_budget * CHARS_PER_TOKEN
    if len(text) <= char_budget:
        return text, False
    # Truncate from the beginning (keep most recent content)
    truncated = text[-(char_budget - 20):]
    # Find first newline to avoid cutting mid-line
    nl = truncated.find("\n")
    if nl > 0 and nl < 100:
        truncated = truncated[nl + 1:]
    return "...(earlier context truncated)\n" + truncated, True


def _truncate_lines_to_budget(lines: list[str], token_budget: int) -> tuple[str, bool]:
    """Keep as many recent lines as fit in the budget."""
    char_budget = token_budget * CHARS_PER_TOKEN
    result = []
    total = 0
    for line in reversed(lines):
        line_len = len(line) + 1  # +1 for newline
        if total + line_len > char_budget:
            break
        result.append(line)
        total += line_len
    result.reverse()
    text = "\n".join(result)
    was_truncated = len(result) < len(lines)
    if was_truncated and result:
        text = f"...(showing last {len(result)} of {len(lines)} items)\n" + text
    return text, was_truncated


# ---------------------------------------------------------------------------
# Layer builders
# ---------------------------------------------------------------------------

def _build_identity_layer(project: Optional[str], budget: int) -> ContextLayer:
    """Project identity: name, stack, conventions."""
    parts: list[str] = []

    if not project:
        return ContextLayer(name="Identity", content="No specific project selected.", priority=5, token_budget=budget)

    parts.append(f"Project: {project}")

    # Load project profile
    try:
        repo = get_project_repo()
        p = repo.get(project)
        if p:
            if p.description:
                parts.append(f"Description: {p.description[:200]}")
            if p.config and isinstance(p.config, dict):
                profile = p.config.get("profile", {})
                if profile.get("stack"):
                    parts.append(f"Stack: {', '.join(profile['stack'])}")
                if profile.get("purpose"):
                    parts.append(f"Purpose: {profile['purpose'][:150]}")
                if profile.get("conventions"):
                    parts.append(f"Conventions: {profile['conventions'][:150]}")
    except Exception:
        pass

    # Load project .ai/rules.md if it exists
    try:
        repo = get_project_repo()
        p = repo.get(project)
        if p and p.path:
            from pathlib import Path
            rules_path = Path(p.path) / ".ai" / "rules.md"
            if rules_path.exists():
                rules = rules_path.read_text()[:600]
                parts.append(f"Project rules:\n{rules}")
    except Exception:
        pass

    content, _ = _truncate_to_budget("\n".join(parts), budget)
    return ContextLayer(name="Identity", content=content, priority=5, token_budget=budget)


def _build_state_layer(
    project: Optional[str],
    channel_id: Optional[str],
    budget: int,
) -> ContextLayer:
    """Current live state: terminals, actions, tasks."""
    parts: list[str] = []

    # Terminals
    try:
        tm = get_terminal_manager()
        terms = [t for t in tm.list() if not project or t.project == project]
        if terms:
            term_lines = []
            for t in terms[:10]:
                waiting = " [WAITING]" if tm.is_waiting_for_input(t.id) else ""
                cmd = t.command or "shell"
                term_lines.append(f"  {t.id[:8]}: {t.project} [{t.status.value}] {cmd}{waiting}")
            parts.append(f"Terminals ({len(terms)}):\n" + "\n".join(term_lines))
    except Exception:
        pass

    # Actions (services + commands)
    try:
        pm = get_process_manager()
        procs = [p for p in pm.list() if not project or p.project == project]
        if procs:
            proc_lines = []
            for p in procs[:10]:
                status = p.status.value if hasattr(p.status, "value") else str(p.status)
                port = f" :{p.port}" if p.port else ""
                proc_lines.append(f"  {p.id}: {p.name} [{status}]{port}")
            parts.append(f"Actions ({len(procs)}):\n" + "\n".join(proc_lines))
    except Exception:
        pass

    # Tasks
    try:
        repo = get_task_repo()
        tasks = repo.list(limit=10)
        if project:
            tasks = [t for t in tasks if getattr(t, "project", None) == project or getattr(t, "project_id", None) == project]
        if tasks:
            task_lines = []
            for t in tasks[:8]:
                status = t.status.value if hasattr(t.status, "value") else str(t.status)
                title = getattr(t, "title", None) or (t.description or "")[:60]
                task_lines.append(f"  [{status}] {title}")
            parts.append(f"Tasks ({len(tasks)}):\n" + "\n".join(task_lines))
    except Exception:
        pass

    # Git state
    if project:
        try:
            repo = get_project_repo()
            p = repo.get(project)
            if p and p.path:
                result = subprocess.run(
                    ["git", "status", "--porcelain", "-b"],
                    capture_output=True, text=True, cwd=p.path, timeout=5,
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    branch_line = lines[0] if lines else ""
                    dirty_count = len([l for l in lines[1:] if l.strip()])
                    parts.append(f"Git: {branch_line}")
                    if dirty_count:
                        parts.append(f"  {dirty_count} uncommitted change(s)")
        except Exception:
            pass

    content, _ = _truncate_to_budget("\n".join(parts), budget)
    return ContextLayer(name="Current State", content=content, priority=4, token_budget=budget)


def _build_recent_layer(
    channel_id: str,
    budget: int,
    message_limit: int = 30,
    event_limit: int = 20,
) -> ContextLayer:
    """Recent channel messages + structured events — full detail."""
    lines: list[str] = []

    # Channel messages (most recent)
    try:
        cm = get_channel_manager()
        messages = cm.list_messages(channel_id, limit=message_limit)
        if messages:
            # Skip system messages that just echo state (State layer covers this)
            _STATE_NOISE = {"Terminal started", "Terminal killed", "Actions executed", "Toggling"}
            lines.append("--- Recent Messages ---")
            seen_content: set[str] = set()
            for msg in messages:
                ts = msg.created_at.strftime("%H:%M") if isinstance(msg.created_at, datetime) else str(msg.created_at)[:5]
                role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                content = (msg.content or "")[:300]

                # Skip noise messages
                if role == "system" and any(content.startswith(n) for n in _STATE_NOISE):
                    continue

                # Dedup identical consecutive messages
                dedup_key = f"{role}:{content[:80]}"
                if dedup_key in seen_content:
                    continue
                seen_content.add(dedup_key)

                # Include metadata summary if present
                meta_hint = ""
                if msg.metadata:
                    mtype = msg.metadata.get("type")
                    if mtype == "action_results":
                        actions = msg.metadata.get("actions", [])
                        action_names = [a.get("action", "?") for a in actions if isinstance(a, dict)]
                        meta_hint = f" [actions: {', '.join(action_names)}]"
                lines.append(f"  [{ts}] {role}: {content}{meta_hint}")
    except Exception:
        pass

    # Structured events (errors, completions, deployments)
    try:
        cm = get_channel_manager()
        events = cm.search_events(channel_id=channel_id, limit=event_limit)
        if events:
            # Deduplicate similar events (keep count)
            deduped: dict[str, dict[str, Any]] = {}
            for evt in events:
                evt_type = evt.get("type", "unknown")
                data = evt.get("data", {})
                # Create a dedup key from type + first 50 chars of data
                key = f"{evt_type}:{str(data)[:50]}"
                if key in deduped:
                    deduped[key]["count"] += 1
                else:
                    deduped[key] = {"type": evt_type, "data": data, "count": 1, "ts": evt.get("timestamp", "")}

            lines.append("--- Recent Events ---")
            for entry in list(deduped.values())[:15]:
                count_str = f" (x{entry['count']})" if entry["count"] > 1 else ""
                data_summary = str(entry["data"])[:120] if entry["data"] else ""
                lines.append(f"  {entry['type']}{count_str}: {data_summary}")
    except Exception:
        pass

    content, truncated = _truncate_lines_to_budget(lines, budget)
    return ContextLayer(name="Recent Activity", content=content, priority=3, token_budget=budget)


def _build_summary_layer(
    project: Optional[str],
    channel_id: str,
    budget: int,
) -> ContextLayer:
    """Compacted older conversation history."""
    parts: list[str] = []

    # Thread summary from conversation manager
    try:
        conv_mgr = get_conversation_manager()
        thread_id = conv_mgr.get_or_create_thread(project)
        thread = conv_mgr.get_thread(thread_id)
        if thread and thread.get("summary"):
            parts.append(thread["summary"][:3000])
    except Exception:
        pass

    # If no thread summary, build one from older channel messages
    if not parts:
        try:
            cm = get_channel_manager()
            # Get older messages (skip the recent ones already in the recent layer)
            all_msgs = cm.list_messages(channel_id, limit=100)
            older = all_msgs[30:] if len(all_msgs) > 30 else []
            if older:
                # Compact: just key decisions and orchestrator responses
                summary_lines = []
                for msg in older:
                    role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                    if role in ("user", "orchestrator"):
                        content = (msg.content or "")[:100]
                        summary_lines.append(f"  {role}: {content}")
                if summary_lines:
                    parts.append("Older conversation (compacted):")
                    parts.extend(summary_lines[-20:])  # Keep last 20 entries
        except Exception:
            pass

    content, _ = _truncate_to_budget("\n".join(parts), budget)
    return ContextLayer(name="History Summary", content=content, priority=1, token_budget=budget)


def _build_git_layer(project: Optional[str], budget: int) -> ContextLayer:
    """Recent git activity."""
    if not project:
        return ContextLayer(name="Git Activity", content="", priority=2, token_budget=budget)

    lines: list[str] = []
    try:
        repo = get_project_repo()
        p = repo.get(project)
        if p and p.path:
            result = subprocess.run(
                ["git", "log", "--oneline", "-15", "--no-decorate"],
                capture_output=True, text=True, cwd=p.path, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines.append("Recent commits:")
                for line in result.stdout.strip().split("\n"):
                    lines.append(f"  {line}")
    except Exception:
        pass

    content, _ = _truncate_lines_to_budget(lines, budget)
    return ContextLayer(name="Git Activity", content=content, priority=2, token_budget=budget)


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------

def assemble_workstream_context(
    channel_id: str,
    project: Optional[str] = None,
    budget: Optional[ContextBudget] = None,
) -> WorkstreamContext:
    """Assemble full workstream context with token-budgeted layers.

    This is the primary entry point. Call this before any LLM interaction
    within a workstream to give the agent full, structured context.

    Args:
        channel_id: The active channel/workstream ID.
        project: The project name (optional, derived from channel if not given).
        budget: Token budget allocation per layer. Uses defaults if not given.

    Returns:
        WorkstreamContext with assembled, prioritized layers.
    """
    if budget is None:
        budget = ContextBudget()

    # Resolve channel info
    channel_name = ""
    if not project:
        try:
            cm = get_channel_manager()
            ch = cm.get_channel(channel_id)
            if ch:
                channel_name = ch.name
                proj_ids = cm.get_channel_projects(channel_id)
                if proj_ids:
                    pr = get_project_repo()
                    p = pr.get(proj_ids[0])
                    if p:
                        project = p.name
        except Exception:
            pass

    # Build all layers
    layers = [
        _build_identity_layer(project, budget.identity),
        _build_state_layer(project, channel_id, budget.state),
        _build_recent_layer(channel_id, budget.recent),
        _build_summary_layer(project, channel_id, budget.summary),
        _build_git_layer(project, budget.git),
    ]

    # Calculate totals
    total_tokens = sum(l.estimated_tokens for l in layers)
    truncated = any(l.estimated_tokens > l.token_budget for l in layers)

    ctx = WorkstreamContext(
        channel_id=channel_id,
        channel_name=channel_name,
        project=project,
        layers=layers,
        total_tokens=total_tokens,
        truncated=truncated,
    )

    logger.debug(
        "Assembled workstream context: channel=%s project=%s tokens=%d layers=%s",
        channel_id, project, total_tokens,
        {l.name: l.estimated_tokens for l in layers},
    )

    return ctx
