"""Canonical NDJSON stream parser for structured CLI agent output.

Normalizes real-time JSON-line events from supported CLI providers
(Claude Code `--output-format stream-json`, Codex CLI `exec --json`)
into a uniform RDC session event schema so desktop, mobile, and kiosk UIs
can render interactive semantic timelines regardless of provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_MESSAGE_CHARS = 4000
MAX_TOOL_SUMMARY_CHARS = 180
MAX_TOOL_OUTPUT_CHARS = 1200


@dataclass(frozen=True)
class NormalizedAgentEvent:
    """Provider-agnostic structured event emitted during an agent turn."""

    event_type: str
    data: dict[str, Any]
    step_label: str | None = None


def _truncate(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _summarize_tool_input(tool_name: str, tool_input: Any) -> str:
    """Create a compact, human-readable 1-line summary of a tool invocation."""
    if not isinstance(tool_input, dict):
        return _truncate(str(tool_input or ""), MAX_TOOL_SUMMARY_CHARS)

    lowered = {str(k).lower(): v for k, v in tool_input.items()}
    for key in (
        "commandline",
        "command",
        "file_path",
        "path",
        "targetfile",
        "pattern",
        "query",
        "url",
        "description",
        "prompt",
    ):
        val = lowered.get(key)
        if isinstance(val, str) and val.strip():
            return _truncate(val.strip().splitlines()[0], MAX_TOOL_SUMMARY_CHARS)

    # Fallback: first short scalar value
    for key, val in tool_input.items():
        if isinstance(val, (str, int, float, bool)) and str(val).strip():
            return _truncate(f"{key}={val}", MAX_TOOL_SUMMARY_CHARS)
    return ""


def _extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                txt = item.get("text") or item.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    if isinstance(content, dict):
        txt = content.get("text") or content.get("content")
        if isinstance(txt, str):
            return txt
    return ""


def _parse_claude_obj(obj: dict[str, Any]) -> tuple[list[NormalizedAgentEvent], str | None]:
    """Parse a single Claude Code `--output-format stream-json` JSON object."""
    events: list[NormalizedAgentEvent] = []
    final_text: str | None = None
    msg_type = obj.get("type")

    if msg_type == "assistant":
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text") or "").strip()
                    if text:
                        events.append(
                            NormalizedAgentEvent(
                                event_type="session.agent_message",
                                data={
                                    "provider": "claude",
                                    "text": _truncate(text, MAX_MESSAGE_CHARS),
                                },
                                step_label="Responding",
                            )
                        )
                elif btype == "thinking":
                    thinking = str(block.get("thinking") or "").strip()
                    if thinking:
                        events.append(
                            NormalizedAgentEvent(
                                event_type="session.agent_thought",
                                data={
                                    "provider": "claude",
                                    "text": _truncate(thinking, MAX_TOOL_OUTPUT_CHARS),
                                },
                                step_label="Thinking",
                            )
                        )
                elif btype == "tool_use":
                    tool = str(block.get("name") or "tool")
                    tool_use_id = str(block.get("id") or "")
                    summary = _summarize_tool_input(tool, block.get("input"))
                    label = f"{tool}: {summary}" if summary else f"Using {tool}"
                    events.append(
                        NormalizedAgentEvent(
                            event_type="session.tool_use",
                            data={
                                "provider": "claude",
                                "tool": tool,
                                "tool_use_id": tool_use_id,
                                "summary": summary,
                            },
                            step_label=_truncate(label, 80),
                        )
                    )

    elif msg_type == "user":
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id") or "")
                is_error = bool(block.get("is_error", False))
                output = _extract_text_from_content(block.get("content"))
                events.append(
                    NormalizedAgentEvent(
                        event_type="session.tool_result",
                        data={
                            "provider": "claude",
                            "tool_use_id": tool_use_id,
                            "is_error": is_error,
                            "output": _truncate(output, MAX_TOOL_OUTPUT_CHARS),
                        },
                    )
                )

    elif msg_type == "result":
        result_str = obj.get("result")
        if isinstance(result_str, str) and result_str.strip():
            final_text = result_str.strip()
        metrics: dict[str, Any] = {
            "provider": "claude",
            "is_error": bool(obj.get("is_error", False)),
        }
        if obj.get("total_cost_usd") is not None:
            metrics["cost_usd"] = obj.get("total_cost_usd")
        elif obj.get("cost_usd") is not None:
            metrics["cost_usd"] = obj.get("cost_usd")
        if obj.get("duration_ms") is not None:
            metrics["duration_ms"] = obj.get("duration_ms")
        if obj.get("num_turns") is not None:
            metrics["num_turns"] = obj.get("num_turns")
        if isinstance(obj.get("usage"), dict):
            metrics["usage"] = obj["usage"]
        events.append(
            NormalizedAgentEvent(
                event_type="session.turn_metrics",
                data=metrics,
            )
        )

    return events, final_text


def _parse_codex_obj(obj: dict[str, Any]) -> tuple[list[NormalizedAgentEvent], str | None]:
    """Parse a single Codex CLI `exec --json` JSON object."""
    events: list[NormalizedAgentEvent] = []
    final_text: str | None = None
    ev_type = obj.get("type")

    if ev_type in ("item.started", "item.completed"):
        item = obj.get("item")
        if not isinstance(item, dict):
            return events, final_text
        itype = item.get("type")
        item_id = str(item.get("id") or "")

        if itype == "agent_message" and ev_type == "item.completed":
            text = str(item.get("text") or "").strip()
            if text:
                final_text = text
                events.append(
                    NormalizedAgentEvent(
                        event_type="session.agent_message",
                        data={
                            "provider": "codex",
                            "text": _truncate(text, MAX_MESSAGE_CHARS),
                        },
                        step_label="Responding",
                    )
                )

        elif itype == "reasoning" and ev_type == "item.completed":
            text = str(item.get("text") or "").strip()
            if text:
                events.append(
                    NormalizedAgentEvent(
                        event_type="session.agent_thought",
                        data={
                            "provider": "codex",
                            "text": _truncate(text, MAX_TOOL_OUTPUT_CHARS),
                        },
                        step_label="Thinking",
                    )
                )

        elif itype == "command_execution":
            cmd = _truncate(str(item.get("command") or ""), MAX_TOOL_SUMMARY_CHARS)
            if ev_type == "item.started":
                events.append(
                    NormalizedAgentEvent(
                        event_type="session.tool_use",
                        data={
                            "provider": "codex",
                            "tool": "Bash",
                            "tool_use_id": item_id,
                            "summary": cmd,
                        },
                        step_label=_truncate(f"Bash: {cmd}" if cmd else "Running command", 80),
                    )
                )
            else:
                exit_code = item.get("exit_code")
                output = str(item.get("aggregated_output") or "")
                is_error = (exit_code is not None and exit_code != 0) or item.get("status") == "failed"
                events.append(
                    NormalizedAgentEvent(
                        event_type="session.tool_result",
                        data={
                            "provider": "codex",
                            "tool": "Bash",
                            "tool_use_id": item_id,
                            "summary": cmd,
                            "exit_code": exit_code,
                            "is_error": is_error,
                            "output": _truncate(output, MAX_TOOL_OUTPUT_CHARS),
                        },
                    )
                )

        elif itype == "file_change" and ev_type == "item.completed":
            raw_changes = item.get("changes")
            changes: list[dict[str, str]] = []
            if isinstance(raw_changes, list):
                for ch in raw_changes[:30]:
                    if isinstance(ch, dict) and ch.get("path"):
                        changes.append(
                            {
                                "path": str(ch["path"]),
                                "kind": str(ch.get("kind") or "update"),
                            }
                        )
            if changes:
                first_path = changes[0]["path"]
                step = f"Edited {first_path}" if len(changes) == 1 else f"Edited {len(changes)} files"
                events.append(
                    NormalizedAgentEvent(
                        event_type="session.file_change",
                        data={
                            "provider": "codex",
                            "changes": changes,
                        },
                        step_label=_truncate(step, 80),
                    )
                )

    elif ev_type == "turn.completed":
        usage = obj.get("usage")
        if isinstance(usage, dict):
            events.append(
                NormalizedAgentEvent(
                    event_type="session.turn_metrics",
                    data={
                        "provider": "codex",
                        "usage": usage,
                    },
                )
            )

    return events, final_text


def parse_stream_line(line: str) -> tuple[list[NormalizedAgentEvent], str | None]:
    """Parse a single line of potential NDJSON output from any supported agent."""
    stripped = line.strip()
    if not stripped or not stripped.startswith("{") or not stripped.endswith("}"):
        return [], None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return [], None
    if not isinstance(obj, dict):
        return [], None

    ev_type = obj.get("type")
    if isinstance(ev_type, str):
        if ev_type in ("assistant", "user", "result", "system"):
            return _parse_claude_obj(obj)
        if ev_type.startswith(("item.", "turn.", "thread.")):
            return _parse_codex_obj(obj)

    return [], None


_AGY_TERMINAL_STATES = frozenset({"DONE", "COMPLETED", "SUCCESS", "FAILED", "ERROR", "CANCELLED"})
_AGY_ERROR_STATES = frozenset({"FAILED", "ERROR"})


class AgentStreamParser:
    """Stateful line-buffered NDJSON parser for a live terminal stream.

    Supports Claude Code (`--output-format stream-json`), Codex CLI (`exec --json`),
    and agy (`--output-format stream-json` with `event: "step_update"` deltas).
    """

    def __init__(self) -> None:
        self._pending: str = ""
        self.last_final_result: str | None = None
        self.live_step_label: str | None = None
        # agy step_update coalescing state
        self._agy_text_step: dict[str, Any] | None = None
        self._agy_seen_tools: set[str] = set()
        self._agy_finished_tools: set[str] = set()

    def _flush_agy_text_step(self) -> list[NormalizedAgentEvent]:
        step = self._agy_text_step
        self._agy_text_step = None
        if not step:
            return []
        text = str(step.get("text") or "").strip()
        if not text:
            return []
        stype = str(step.get("step_type") or "")
        if "think" in stype or "reason" in stype:
            return [
                NormalizedAgentEvent(
                    event_type="session.agent_thought",
                    data={"provider": "agy", "text": _truncate(text, MAX_TOOL_OUTPUT_CHARS)},
                    step_label="Thinking",
                )
            ]
        self.last_final_result = text
        return [
            NormalizedAgentEvent(
                event_type="session.agent_message",
                data={"provider": "agy", "text": _truncate(text, MAX_MESSAGE_CHARS)},
                step_label="Responding",
            )
        ]

    def _handle_agy_obj(self, obj: dict[str, Any]) -> list[NormalizedAgentEvent]:
        emitted: list[NormalizedAgentEvent] = []
        ev_name = str(obj.get("event") or "")

        if ev_name == "step_update":
            su = obj.get("step_update")
            if not isinstance(su, dict):
                return emitted
            step_index = su.get("step_index")
            step_key = f"agy-{step_index}" if step_index is not None else "agy-step"
            step_type = str(su.get("step_type") or "").lower()
            state = str(su.get("state") or "").upper()

            if step_type in ("user_input", "system", "init"):
                return emitted

            is_text_step = any(k in step_type for k in ("response", "message", "think", "reason"))
            delta = su.get("text_delta") or su.get("thinking_delta") or su.get("delta") or ""
            full_text = su.get("text") or su.get("content") or ""

            if is_text_step:
                # If a different text step was open, flush it first
                if self._agy_text_step and self._agy_text_step.get("step_index") != step_index:
                    emitted.extend(self._flush_agy_text_step())

                if self._agy_text_step is None:
                    self._agy_text_step = {
                        "step_index": step_index,
                        "step_type": step_type,
                        "text": "",
                    }
                    self.live_step_label = (
                        "Thinking…" if ("think" in step_type or "reason" in step_type) else "Writing response…"
                    )
                if isinstance(delta, str) and delta:
                    self._agy_text_step["text"] += delta
                elif isinstance(full_text, str) and full_text and len(full_text) >= len(self._agy_text_step["text"]):
                    self._agy_text_step["text"] = full_text

                if state in _AGY_TERMINAL_STATES:
                    emitted.extend(self._flush_agy_text_step())
                    if isinstance(su.get("usage"), dict):
                        dur_s = su.get("duration_seconds")
                        emitted.append(
                            NormalizedAgentEvent(
                                event_type="session.turn_metrics",
                                data={
                                    "provider": "agy",
                                    "usage": su["usage"],
                                    **(
                                        {"duration_ms": int(float(dur_s) * 1000)}
                                        if isinstance(dur_s, (int, float))
                                        else {}
                                    ),
                                },
                            )
                        )
                return emitted

            # Non-text step (tool call / command / file operation)
            if self._agy_text_step is not None:
                emitted.extend(self._flush_agy_text_step())

            tool_info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
            tool_name = str(su.get("tool_name") or tool_info.get("name") or su.get("tool") or step_type or "tool")
            tool_input = (
                tool_info.get("parameters")
                or su.get("input")
                or su.get("args")
                or su.get("arguments")
                or su
            )
            summary = _summarize_tool_input(tool_name, tool_input)

            if step_key not in self._agy_seen_tools:
                self._agy_seen_tools.add(step_key)
                label = f"{tool_name}: {summary}" if summary else f"Using {tool_name}"
                emitted.append(
                    NormalizedAgentEvent(
                        event_type="session.tool_use",
                        data={
                            "provider": "agy",
                            "tool": tool_name,
                            "tool_use_id": step_key,
                            "summary": summary,
                        },
                        step_label=_truncate(label, 80),
                    )
                )

            if state in _AGY_TERMINAL_STATES and step_key not in self._agy_finished_tools:
                self._agy_finished_tools.add(step_key)
                output = str(tool_info.get("output") or su.get("output") or su.get("result") or su.get("error") or "")
                emitted.append(
                    NormalizedAgentEvent(
                        event_type="session.tool_result",
                        data={
                            "provider": "agy",
                            "tool": tool_name,
                            "tool_use_id": step_key,
                            "summary": summary,
                            "is_error": state in _AGY_ERROR_STATES or bool(su.get("error")),
                            "output": _truncate(output, MAX_TOOL_OUTPUT_CHARS),
                        },
                    )
                )
            return emitted

        # Terminal / result events from agy
        if ev_name in ("result", "turn_complete", "completed", "done", "final"):
            emitted.extend(self._flush_agy_text_step())
            result_text = obj.get("result") or obj.get("text") or obj.get("response")
            if isinstance(result_text, str) and result_text.strip():
                self.last_final_result = result_text.strip()
            metrics: dict[str, Any] = {
                "provider": "agy",
                "is_error": bool(obj.get("is_error") or obj.get("error")),
            }
            if obj.get("duration_ms") is not None:
                metrics["duration_ms"] = obj.get("duration_ms")
            if obj.get("cost_usd") is not None:
                metrics["cost_usd"] = obj.get("cost_usd")
            if isinstance(obj.get("usage"), dict):
                metrics["usage"] = obj["usage"]
            emitted.append(NormalizedAgentEvent(event_type="session.turn_metrics", data=metrics))

        return emitted

    def _parse_line_stateful(self, line: str) -> list[NormalizedAgentEvent]:
        stripped = line.strip()
        if not stripped or not stripped.startswith("{") or not stripped.endswith("}"):
            return []
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(obj, dict):
            return []

        if isinstance(obj.get("event"), str):
            return self._handle_agy_obj(obj)

        ev_type = obj.get("type")
        if isinstance(ev_type, str):
            if ev_type in ("assistant", "user", "result", "system"):
                parsed, final_text = _parse_claude_obj(obj)
                if final_text:
                    self.last_final_result = final_text
                return parsed
            if ev_type.startswith(("item.", "turn.", "thread.")):
                parsed, final_text = _parse_codex_obj(obj)
                if final_text:
                    self.last_final_result = final_text
                return parsed
        return []

    def feed(self, chunk: str) -> list[NormalizedAgentEvent]:
        """Feed newly received ANSI-stripped text; return newly parsed events."""
        if not chunk:
            return []
        normalized = chunk.replace("\r\n", "\n").replace("\r", "\n")
        self._pending += normalized
        if "\n" not in self._pending:
            # Cap pending buffer so non-JSON huge lines cannot grow unbounded
            if len(self._pending) > 256 * 1024:
                self._pending = self._pending[-32 * 1024 :]
            return []

        lines = self._pending.split("\n")
        self._pending = lines.pop()

        emitted: list[NormalizedAgentEvent] = []
        for line in lines:
            parsed = self._parse_line_stateful(line)
            if parsed:
                emitted.extend(parsed)
        return emitted

    def flush(self) -> list[NormalizedAgentEvent]:
        """Parse any trailing buffered line and flush open agy steps on stream end."""
        emitted: list[NormalizedAgentEvent] = []
        if self._pending.strip():
            emitted.extend(self._parse_line_stateful(self._pending))
        self._pending = ""
        emitted.extend(self._flush_agy_text_step())
        return emitted


def extract_final_from_ndjson(text: str) -> str | None:
    """Extract the final human-readable assistant answer from an NDJSON transcript."""
    parser = AgentStreamParser()
    events = parser.feed(text + "\n")
    events.extend(parser.flush())
    if parser.last_final_result:
        return parser.last_final_result
    last_message: str | None = None
    for ev in events:
        if ev.event_type == "session.agent_message":
            msg = ev.data.get("text")
            if isinstance(msg, str) and msg.strip():
                last_message = msg.strip()
    return last_message
