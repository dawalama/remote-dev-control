"""Tests for structured NDJSON agent stream parsing and command wiring."""

import json

from remote_dev_ctrl.server.agent_registry import build_agent_command
from remote_dev_ctrl.server.agent_stream import (
    AgentStreamParser,
    extract_final_from_ndjson,
)
from remote_dev_ctrl.server.session_manager import extract_agent_report


def test_claude_stream_json_parsing_and_chunked_lines():
    parser = AgentStreamParser()

    line1 = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Let me inspect the test suite."},
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": "Bash",
                        "input": {"command": "pytest tests/test_agent_stream.py"},
                    },
                ]
            },
        }
    )
    line2 = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "is_error": False,
                        "content": "3 passed in 0.12s",
                    }
                ]
            },
        }
    )
    line3 = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "All tests are passing."}]
            },
        }
    )
    line4 = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": "All tests are passing.",
            "total_cost_usd": 0.0123,
            "duration_ms": 4200,
            "num_turns": 2,
        }
    )

    # Split mid-JSON line across two chunks to verify line buffering
    stream = "\n".join([line1, line2, line3, line4]) + "\n"
    mid = len(line1) // 2
    events = parser.feed(stream[:mid])
    assert events == []

    events = parser.feed(stream[mid:])
    types = [e.event_type for e in events]
    assert types == [
        "session.agent_thought",
        "session.tool_use",
        "session.tool_result",
        "session.agent_message",
        "session.turn_metrics",
    ]

    tool_ev = events[1]
    assert tool_ev.data["tool"] == "Bash"
    assert tool_ev.data["tool_use_id"] == "toolu_01"
    assert "pytest" in tool_ev.data["summary"]
    assert tool_ev.step_label and "Bash:" in tool_ev.step_label

    res_ev = events[2]
    assert res_ev.data["tool_use_id"] == "toolu_01"
    assert res_ev.data["is_error"] is False
    assert "3 passed" in res_ev.data["output"]

    metrics_ev = events[4]
    assert metrics_ev.data["cost_usd"] == 0.0123
    assert metrics_ev.data["num_turns"] == 2
    assert parser.last_final_result == "All tests are passing."


def test_codex_json_stream_parsing():
    parser = AgentStreamParser()
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "th_1"}),
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "cmd_1",
                    "type": "command_execution",
                    "command": "uv run pytest",
                    "status": "in_progress",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd_1",
                    "type": "command_execution",
                    "command": "uv run pytest",
                    "exit_code": 0,
                    "aggregated_output": "10 passed",
                    "status": "completed",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "fc_1",
                    "type": "file_change",
                    "changes": [{"path": "src/app.py", "kind": "update"}],
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "msg_1",
                    "type": "agent_message",
                    "text": "Updated `src/app.py` and verified with pytest.",
                },
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 1200, "output_tokens": 340},
            }
        ),
    ]

    events = parser.feed("\n".join(lines) + "\n")
    assert [e.event_type for e in events] == [
        "session.tool_use",
        "session.tool_result",
        "session.file_change",
        "session.agent_message",
        "session.turn_metrics",
    ]
    assert events[0].data["tool"] == "Bash"
    assert events[1].data["exit_code"] == 0
    assert events[2].data["changes"] == [{"path": "src/app.py", "kind": "update"}]
    assert events[4].data["usage"]["output_tokens"] == 340


def test_extract_agent_report_unwraps_claude_ndjson_block():
    ndjson_payload = "\n".join(
        [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Working..."}]}}),
            json.dumps({"type": "result", "result": "# Done\n\nFixed the bug."}),
        ]
    )
    transcript = f"noise\n__RDC_FINAL_BEGIN__\n{ndjson_payload}\n__RDC_FINAL_END__\n__RDC_EXIT:0\n"
    assert extract_agent_report(transcript, "claude") == "# Done\n\nFixed the bug."
    assert extract_final_from_ndjson(ndjson_payload) == "# Done\n\nFixed the bug."


def test_agy_step_update_stream_coalescing_and_report_extraction():
    parser = AgentStreamParser()
    lines = [
        json.dumps({"event": "init", "conversation_id": "conv-1", "init": {"cwd": "/tmp"}}),
        json.dumps({
            "event": "step_update",
            "step_update": {"conversation_id": "conv-1", "step_index": 0, "state": "DONE", "step_type": "user_input"},
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "read_file",
                "tool_info": {"name": "read_file", "parameters": {"Path": "src/dotai/cli/agents_cmd.py"}},
            },
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 2,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "read_file",
                "duration_seconds": 0.73,
                "tool_info": {
                    "name": "read_file",
                    "parameters": {"Path": "src/dotai/cli/agents_cmd.py"},
                    "output": "57 lines, 4387 bytes",
                },
            },
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 26,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Hardcodes ",
            },
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 26,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "`claude` as the only adapter.",
            },
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-1",
                "step_index": 26,
                "state": "DONE",
                "step_type": "agent_response",
                "duration_seconds": 6.2,
                "usage": {"input_tokens": 10089, "output_tokens": 495},
            },
        }),
    ]
    ndjson = "\n".join(lines) + "\n"
    events = parser.feed(ndjson)
    assert [e.event_type for e in events] == [
        "session.tool_use",
        "session.tool_result",
        "session.agent_message",
        "session.turn_metrics",
    ]
    assert events[0].data["tool"] == "read_file"
    assert events[0].data["summary"] == "src/dotai/cli/agents_cmd.py"
    assert events[1].data["output"] == "57 lines, 4387 bytes"
    assert events[2].data["text"] == "Hardcodes `claude` as the only adapter."
    assert events[3].data["usage"]["output_tokens"] == 495

    transcript = f"\n__RDC_FINAL_BEGIN__\n{ndjson}\n__RDC_FINAL_END__\n__RDC_EXIT:0\n"
    assert extract_agent_report(transcript, "agy") == "Hardcodes `claude` as the only adapter."


def test_build_agent_command_enables_structured_streaming():
    claude_cmd = build_agent_command("claude", "Check status")
    assert "--output-format stream-json --verbose" in claude_cmd
    assert "tee \"$rdc_final\"" in claude_cmd

    codex_cmd = build_agent_command("codex", "Check status")
    assert "exec --sandbox workspace-write --json" in codex_cmd
    assert "--output-last-message \"$rdc_final\"" in codex_cmd

    agy_cmd = build_agent_command("agy", "Check status")
    assert "--dangerously-skip-permissions --output-format stream-json --print 'Check status'" in agy_cmd
    assert "tee \"$rdc_final\"" in agy_cmd


def test_steer_endpoint_queues_while_running_and_injects_into_next_loop_round(monkeypatch, tmp_path):
    import asyncio
    from fastapi.testclient import TestClient
    from remote_dev_ctrl.server.app import app
    from remote_dev_ctrl.server.auth import Role, get_auth_manager
    from remote_dev_ctrl.server.session_loop import (
        get_pending_steers,
        handle_agent_turn_completed,
    )
    from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager

    sm = get_session_manager()
    session = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Fix failing test",
        metadata={"supervised": True, "verification_command": "pytest"},
        budget={"max_rounds": 3},
        status=SessionStatus.RUNNING,
    )
    token, _ = get_auth_manager().create_token("steer-test", Role.ADMIN)
    client = TestClient(app)

    resp = client.post(
        f"/sessions/{session.id}/steer",
        headers={"Authorization": f"Bearer {token}"},
        json={"guidance": "Check timezone handling in parser.py"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "queued"
    assert len(body["pending_steers"]) == 1
    assert "timezone handling" in body["pending_steers"][0]["text"]

    # Simulate verification failure on round 1 -> loop retries and injects operator steer
    captured_instructions: list[str] = []
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_loop.effective_session_path",
        lambda _s: tmp_path,
    )
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_loop.verification_runtime_blocker",
        lambda _c, _cwd, _root: None,
    )
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_loop.run_verification_sync",
        lambda _s, _cmd, source="rdc-loop": {
            "id": "ev-1",
            "evidence_type": "verification",
            "data": {
                "exit_code": 1,
                "duration_seconds": 0.2,
                "concise_feedback": "FAILED test_parser.py::test_tz",
                "signature": "sig-tz-1",
            },
        },
    )
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_loop._fetch_proven_patterns_hint",
        lambda _s: "",
    )
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_controls.instruct_session",
        lambda sid, instruction: captured_instructions.append(instruction) or {"success": True},
    )

    fresh = sm.get(session.id)
    assert fresh is not None
    res = asyncio.run(handle_agent_turn_completed(fresh, "Tried initial fix", agent_exit_code=0))
    assert res["action"] == "retry"
    assert res["round"] == 2
    assert len(captured_instructions) == 1
    assert "OPERATOR STEERING GUIDANCE" in captured_instructions[0]
    assert "Check timezone handling in parser.py" in captured_instructions[0]
    assert get_pending_steers(sm.get(session.id)) == []

