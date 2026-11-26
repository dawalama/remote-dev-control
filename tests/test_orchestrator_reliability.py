"""Regression tests for orchestrator LLM reliability and noise filtering."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from remote_dev_ctrl.server.channel_manager import get_channel_manager
from remote_dev_ctrl.server.intent import (
    IntentEngine,
    OrchestratorContext,
    load_nanobot_config,
)
from remote_dev_ctrl.server.workstream_context import _build_recent_layer


def test_load_nanobot_config_migrates_retired_models(monkeypatch, tmp_path):
    cfg_path = tmp_path / "nanobot.json"
    cfg_path.write_text(json.dumps({
        "model_fast": "google/gemini-2.0-flash-001",
        "model_mid": "anthropic/claude-sonnet-4",
        "model_overrides": {"model_fast": "google/gemini-2.0-flash-001"},
    }))
    monkeypatch.setattr("remote_dev_ctrl.server.intent._nanobot_config_path", lambda: cfg_path)

    cfg = load_nanobot_config()
    assert cfg["model_fast"] == "google/gemini-2.5-flash"
    assert cfg["model_overrides"]["model_fast"] == "google/gemini-2.5-flash"


def test_orchestrator_tool_replay_strips_unsupported_sdk_fields(monkeypatch):
    """Ensure round-2 assistant tool_call replay never sends `audio=None` / `refusal=None` (Groq 400 bug)."""
    captured_payloads: list[list[dict]] = []

    class FakeToolCall:
        id = "call_1"

        class function:
            name = "run_command"
            arguments = '{"command": "git status --short"}'

    class FakeMessage1:
        role = "assistant"
        content = None
        tool_calls = [FakeToolCall()]

        def model_dump(self):
            return {
                "role": "assistant",
                "content": None,
                "audio": None,
                "refusal": None,
                "annotations": None,
                "function_call": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "run_command", "arguments": '{"command": "git status --short"}'}}
                ],
            }

    class FakeMessage2:
        role = "assistant"
        content = "Everything is clean on main with no uncommitted changes."
        tool_calls = None

    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            captured_payloads.append(kwargs["messages"])
            msg = FakeMessage1() if self.calls == 1 else FakeMessage2()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg)],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    engine = IntentEngine()
    monkeypatch.setattr(engine, "_get_client", lambda: fake_client)

    async def fake_execute(name, params, ctx):
        return {"action": name, "success": True, "git_dirty": False}

    monkeypatch.setattr(
        "remote_dev_ctrl.server.intent.get_action_executor",
        lambda: SimpleNamespace(execute=fake_execute),
    )
    monkeypatch.setattr(
        "remote_dev_ctrl.server.conversation.get_conversation_manager",
        lambda: SimpleNamespace(
            get_or_create_thread=lambda _proj: "thread-test",
            get_thread=lambda _tid: {"summary": None},
            get_recent_turns=lambda _tid, n=20: [],
        ),
    )

    ctx = OrchestratorContext(project="demo")
    stages: list[str] = []

    async def capture_stage(stage: str):
        stages.append(stage)

    result = asyncio.run(
        engine.process("explain what is happening in the project?", ctx, on_progress=capture_stage)
    )
    assert "clean" in result.response
    assert len(captured_payloads) == 2
    assert stages[0] == "Thinking…"
    assert any("git status" in s for s in stages)
    assert "Analyzing results…" in stages

    # Inspect round 2 messages — assistant message must NOT include `audio`, `refusal`, `annotations`, `function_call`
    round2_assistant = [m for m in captured_payloads[1] if m.get("role") == "assistant"][-1]
    assert set(round2_assistant.keys()) == {"role", "content", "tool_calls"}
    assert "audio" not in round2_assistant
    assert "refusal" not in round2_assistant


def test_workstream_recent_layer_filters_noisy_session_activity():
    cm = get_channel_manager()
    ch = cm.create_channel(name="noise-filter-test")
    cm.post_message(
        ch.id,
        role="system",
        content="Session started: noisy card",
        metadata={"type": "a2ui", "activity": True, "components": [{"type": "task_card", "title": "Goal"}]},
    )
    cm.post_message(
        ch.id,
        role="system",
        content="HomeBase session s1 · Status: verifying · Goal: noisy notify",
        metadata={"type": "session_notify", "session_id": "s1"},
    )
    cm.post_message(ch.id, role="user", content="Any uncommitted work?")
    cm.post_message(ch.id, role="orchestrator", content="Two files are modified on main.")

    layer = _build_recent_layer(ch.id, budget=1500)
    assert "Any uncommitted work?" in layer.content
    assert "Two files are modified on main." in layer.content
    assert "noisy card" not in layer.content
    assert "noisy notify" not in layer.content


def test_scoped_project_guard_blocks_cross_project_hallucination():
    from remote_dev_ctrl.server.intent import _resolve_scoped_project

    ctx = OrchestratorContext(
        project="documaker",
        active_workstream="documaker",
        active_workstream_id="ch-documaker",
        workstreams=[{"id": "ch-documaker", "name": "documaker", "project_names": ["documaker"]}],
        user_message="Is there any uncommitted change in the project folder?",
    )
    # LLM hallucinated another project — must clamp to active workstream project
    assert _resolve_scoped_project("truesteps-site", ctx) == "documaker"
    assert _resolve_scoped_project(None, ctx) == "documaker"

    # Explicit user mention of another project IS allowed
    ctx.user_message = "Check truesteps-site for uncommitted changes"
    assert _resolve_scoped_project("truesteps-site", ctx) == "truesteps-site"

    # Whole-HomeBase (#system) scope allows targeting any project
    homebase_ctx = OrchestratorContext(
        project="documaker",
        active_workstream="system",
        active_workstream_id="ch-system",
        user_message="Check uncommitted changes",
    )
    assert _resolve_scoped_project("truesteps-site", homebase_ctx) == "truesteps-site"


def test_homebase_brief_uses_human_channel_name_instead_of_raw_id():
    from remote_dev_ctrl.server.brief import build_homebase_brief

    cm = get_channel_manager()
    ch = cm.create_channel(name="documaker")
    brief = build_homebase_brief(workstream_id=ch.id)
    assert "#documaker" in brief["spoken_summary"]
    assert ch.id not in brief["spoken_summary"]

