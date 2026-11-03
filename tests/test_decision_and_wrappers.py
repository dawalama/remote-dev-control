import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from remote_dev_ctrl.server.agent_registry import (
    AgentWrapperSpec,
    build_agent_command,
    discover_model_providers,
    get_model_tier_matrix,
    get_wrapper_self_registration_instructions,
    list_registered_wrappers,
    register_dropin_wrapper,
    remove_wrapper_spec,
    upsert_wrapper_spec,
)
from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.decision import (
    COMMAND_RISK_QUESTIONS,
    DecisionEngine,
    evaluate_command_risk,
    evaluate_intent_routing,
    evaluate_session_triage,
    get_decision_engine,
)


def test_decision_engine_primitives_and_schema():
    engine = get_decision_engine()
    state = {"command": "rm -rf /", "project": "demo"}
    res = engine.evaluate(state=state, questions=COMMAND_RISK_QUESTIONS)

    assert res.provider == "rdc_calibrated_system1"
    assert res.latency_ms >= 0
    results = res.results

    # Noul: float in [0.0, 1.0]
    assert isinstance(results["is_destructive"], float)
    assert results["is_destructive"] > 0.85

    # Score: level, score, probabilities, confidence
    score_res = results["blast_radius"]
    assert score_res["type"] == "score"
    assert score_res["level"] == "external_or_irreversible"
    assert score_res["confidence"] > 0.5
    assert "read_only" in score_res["probabilities"]

    # Choice: value, probabilities, confidence
    choice_res = results["approval_policy"]
    assert choice_res["type"] == "choice"
    assert choice_res["value"] == "ask_operator"
    assert choice_res["confidence"] > 0.5


def test_command_risk_safe_vs_destructive():
    safe_eval = evaluate_command_risk("git diff HEAD")["results"]
    assert safe_eval["is_destructive"] < 0.15
    assert safe_eval["blast_radius"]["level"] == "read_only"
    assert safe_eval["approval_policy"]["value"] == "auto_approve"

    danger_eval = evaluate_command_risk("git reset --hard HEAD~1")["results"]
    assert danger_eval["is_destructive"] > 0.80
    assert danger_eval["approval_policy"]["value"] == "ask_operator"


def test_session_triage_decision():
    # Blocked by sandbox .git write
    triage = evaluate_session_triage(
        goal="Fix performance bug",
        status="verifying",
        output_summary="`npm run verify` passes: 141 tests.\nCommit and push are blocked: this session cannot write to `.git`.",
        dirty=True,
        has_remote=False,
        has_git_evidence=False,
        has_verification_evidence=True,
        verification_command="npm run verify",
    )["results"]

    assert triage["outcome_satisfied"] > 0.80
    assert triage["ready_for_host_commit"] > 0.90
    assert triage["blocker_type"]["value"] == "sandbox_git_blocked"
    assert triage["recommended_action"]["value"] == "host_commit"


def test_intent_routing_decision():
    # Trivial UI intent
    trivial = evaluate_intent_routing("toggle sidebar")["results"]
    assert trivial["complexity"]["level"] == "trivial"
    assert trivial["recommended_tier"]["value"] in ("decision", "fast")

    # Coding implementation intent
    coding = evaluate_intent_routing("Implement the Stripe webhook handler and add test cases")["results"]
    assert coding["complexity"]["level"] in ("complex", "reasoning")
    assert coding["recommended_tier"]["value"] == "coding"

    # Deep reasoning intent
    reasoning = evaluate_intent_routing("Explain the root cause and subtle race conditions between our workers")["results"]
    assert reasoning["complexity"]["level"] == "reasoning"
    assert reasoning["recommended_tier"]["value"] == "reasoning"


def test_model_providers_catalog_and_matrix():
    providers = discover_model_providers()
    prov_ids = {p["id"] for p in providers}
    assert "ollama" in prov_ids
    assert "self_hosted" in prov_ids
    assert "typesafe_jev" in prov_ids
    assert "openrouter" in prov_ids

    matrix = get_model_tier_matrix()
    tier_names = [t["tier"] for t in matrix]
    assert tier_names == ["decision", "fast", "coding", "reasoning"]


def test_dropin_wrapper_registration_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("remote_dev_ctrl.server.agent_registry.get_rdc_home", lambda: tmp_path)
    monkeypatch.setattr("remote_dev_ctrl.server.config.get_rdc_home", lambda: tmp_path)

    spec = AgentWrapperSpec(
        id="aider-test",
        label="Aider Test",
        driver="aider",
        model_provider="ollama",
        default_model="qwen3-coder:32b",
        model_tier="coding",
        hosting="local",
        executable="aider",
        terminal_command="aider --model qwen3-coder:32b",
    )
    manifest_path = register_dropin_wrapper(spec)
    assert manifest_path.exists()
    assert "aider-test.json" in manifest_path.name

    # Check list_registered_wrappers discovers dropin
    wrappers = list_registered_wrappers(include_disabled=True)
    found = [w for w in wrappers if w.id == "aider-test"]
    assert len(found) == 1
    assert found[0].source == "dropin"
    assert found[0].hosting == "local"

    # Command builder for aider
    cmd = build_agent_command("aider-test", "Optimize query")
    assert "aider" in cmd
    assert "--yes-always" in cmd
    assert "__RDC_FINAL_BEGIN__" in cmd

    # Remove dropin
    assert remove_wrapper_spec("aider-test") is True
    assert not manifest_path.exists()


def test_wrapper_self_registration_instructions():
    info = get_wrapper_self_registration_instructions()
    assert "dropin_directory" in info
    assert "mcp_tool" in info
    assert info["mcp_tool"] == "register_agent_wrapper"
    assert "agent_prompt" in info
    assert "Method 1 (MCP Tool" in info["agent_prompt"]
    assert "Method 2 (Drop-in JSON Manifest" in info["agent_prompt"]


def test_mcp_tools_and_resource():
    import asyncio
    import mcp.types as types
    from remote_dev_ctrl.mcp.server import create_server

    async def _test():
        server = create_server()
        tool_call_handler = server.request_handlers.get(types.CallToolRequest)
        assert tool_call_handler is not None

        # Test evaluate_decision tool execution
        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="evaluate_decision",
                arguments={
                    "state": {"command": "git push --force origin main"},
                    "questions": COMMAND_RISK_QUESTIONS,
                },
            ),
        )
        res = await tool_call_handler(req)
        content = res.root.content[0]
        parsed = json.loads(content.text)
        assert parsed["results"]["is_destructive"] > 0.8

        # Test register_agent_wrapper tool execution
        reg_req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="register_agent_wrapper",
                arguments={
                    "id": "mcp-test-wrapper",
                    "label": "MCP Test Wrapper",
                    "driver": "aider",
                    "model_provider": "ollama",
                    "default_model": "qwen3-coder:32b",
                },
            ),
        )
        reg_res = await tool_call_handler(reg_req)
        reg_parsed = json.loads(reg_res.root.content[0].text)
        assert reg_parsed["registered"] is True
        assert reg_parsed["wrapper"]["id"] == "mcp-test-wrapper"

    asyncio.run(_test())


def test_api_endpoints_for_models_and_decision():
    token, _ = get_auth_manager().create_token("decision-api-test", Role.ADMIN)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Instructions endpoint
    instr_resp = client.get("/settings/agent-providers/instructions", headers=headers)
    assert instr_resp.status_code == 200
    instr_data = instr_resp.json()
    assert "mcp_tool" in instr_data
    assert instr_data["mcp_tool"] == "register_agent_wrapper"

    # 2. Model catalog & tiers
    models_resp = client.get("/settings/model-providers", headers=headers)
    assert models_resp.status_code == 200
    models_data = models_resp.json()
    assert "providers" in models_data
    assert "tiers" in models_data
    assert any(p["id"] == "typesafe_jev" for p in models_data["providers"])

    # 3. Decision evaluate endpoint
    dec_resp = client.post(
        "/decision/evaluate",
        headers=headers,
        json={
            "state": {"command": "pytest -q"},
            "questions": {
                "safe": {"type": "noul", "instructions": "Is this test command safe?"},
            },
        },
    )
    assert dec_resp.status_code == 200
    dec_data = dec_resp.json()
    assert "results" in dec_data
    assert "safe" in dec_data["results"]
