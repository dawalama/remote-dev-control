"""Tests for issue-type workflow recipes, multi-model tier routing, and stall escalation."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from remote_dev_ctrl.server.agent_registry import (
    pick_reviewer_provider,
    resolve_provider_for_tier,
)
from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.recipes import (
    BUILTIN_RECIPES,
    apply_recipe_to_goal,
    classify_goal_recipe,
    list_recipes,
)
from remote_dev_ctrl.server.session_loop import handle_agent_turn_completed
from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager
from remote_dev_ctrl.server.supervised_sessions import session_execution_prompt


def test_builtin_recipes_and_issue_classification():
    assert {"bugfix", "feature", "refactor", "design-plan", "code-review", "code-audit"} <= set(
        BUILTIN_RECIPES.keys()
    )
    assert classify_goal_recipe("Fix 500 crash when session has no worktree")["recipe_id"] == "bugfix"
    assert classify_goal_recipe("Plan architecture options for multi-model routing")["recipe_id"] == "design-plan"
    assert classify_goal_recipe("Review the diff for race conditions")["recipe_id"] == "code-review"
    assert classify_goal_recipe("Refactor terminal relay to reduce duplication")["recipe_id"] == "refactor"
    assert classify_goal_recipe("Run a security audit on auth middleware")["recipe_id"] == "code-audit"
    assert classify_goal_recipe("Add keyboard shortcut for quick steering")["recipe_id"] == "feature"

    recipes = list_recipes()
    by_id = {r["id"]: r for r in recipes}
    assert by_id["design-plan"]["review_mode"] == "advisory"
    assert by_id["code-review"]["review_mode"] == "advisory"
    assert by_id["bugfix"]["review_mode"] == "implementation"

    wrapped = apply_recipe_to_goal("bugfix", "demo-proj", "Fix flaky auth token refresh")
    assert "Reproduce first" in wrapped
    assert "Fix flaky auth token refresh" in wrapped


def test_cross_model_reviewer_and_tier_routing():
    installed = {"codex": "/usr/bin/codex", "claude": "/usr/bin/claude"}

    def which_fn(exe: str) -> str | None:
        return installed.get(exe)

    # Reviewer must pick a different provider family from author when available
    assert pick_reviewer_provider("codex", which_fn=which_fn) == "claude"
    assert pick_reviewer_provider("claude", which_fn=which_fn) == "codex"

    # Reasoning tier prefers claude over codex; max_quality upgrades coding -> reasoning
    assert resolve_provider_for_tier("reasoning", which_fn=which_fn) == "claude"
    assert (
        resolve_provider_for_tier(
            "coding", routing_profile="max_quality", preferred_fallback="codex", which_fn=which_fn
        )
        == "claude"
    )
    assert (
        resolve_provider_for_tier(
            "coding", routing_profile="balanced", preferred_fallback="codex", which_fn=which_fn
        )
        == "codex"
    )


def test_classify_endpoint_and_recipe_execution_prompt():
    token, _ = get_auth_manager().create_token("recipe-api-test", Role.ADMIN)
    client = TestClient(app)

    resp = client.post(
        "/recipes/classify",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Fix broken WebSocket reconnect on mobile"},
    )
    assert resp.status_code == 200
    assert resp.json()["recipe_id"] == "bugfix"

    sm = get_session_manager()
    sess = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Fix broken WebSocket reconnect on mobile",
        metadata={"supervised": True, "recipe_id": "bugfix", "routing_profile": "balanced"},
    )
    prompt = session_execution_prompt(sess)
    assert "Reproduce first" in prompt
    assert "Fix broken WebSocket reconnect on mobile" in prompt


def test_balanced_loop_auto_escalates_on_stall(monkeypatch, tmp_path):
    sm = get_session_manager()
    session = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Fix subtle concurrency bug",
        agent_provider="codex",
        metadata={
            "supervised": True,
            "verification_command": "pytest",
            "routing_profile": "balanced",
            "loop": {
                "round": 2,
                "max_rounds": 3,
                "history": [
                    {"round": 1, "exit_code": 1, "signature": "sig-same-err"},
                ],
            },
        },
        budget={"max_rounds": 3},
        status=SessionStatus.RUNNING,
    )

    monkeypatch.setattr("remote_dev_ctrl.server.session_loop.effective_session_path", lambda _s: tmp_path)
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_loop.verification_runtime_blocker", lambda _c, _cwd, _r: None
    )
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_loop.run_verification_sync",
        lambda _s, _cmd, source="rdc-loop": {
            "id": "ev-stall",
            "evidence_type": "verification",
            "data": {
                "exit_code": 1,
                "duration_seconds": 0.2,
                "concise_feedback": "FAILED test_race.py::test_lock",
                "signature": "sig-same-err",
            },
        },
    )
    monkeypatch.setattr("remote_dev_ctrl.server.session_loop._fetch_proven_patterns_hint", lambda _s: "")
    monkeypatch.setattr(
        "remote_dev_ctrl.server.agent_registry.resolve_provider_for_tier",
        lambda tier, **kw: "claude" if tier == "reasoning" else "codex",
    )
    monkeypatch.setattr("remote_dev_ctrl.server.session_manager.agent_provider_blocker", lambda _p: None)

    captured: list[str] = []
    monkeypatch.setattr(
        "remote_dev_ctrl.server.session_controls.instruct_session",
        lambda sid, instruction: captured.append(instruction) or {"success": True},
    )

    res = asyncio.run(handle_agent_turn_completed(session, "Attempt 2", agent_exit_code=0))
    assert res["action"] == "retry"
    assert res["round"] == 3
    updated = sm.get(session.id)
    assert updated is not None
    assert updated.agent_provider == "claude"
    assert updated.metadata["loop"]["escalated_from"] == "codex"
    assert updated.metadata["loop"]["escalated_provider"] == "claude"
    assert len(captured) == 1
    assert "REASONING ESCALATION" in captured[0]


def test_advisory_review_does_not_collide_with_verifying_parent_worktree(tmp_path):
    from remote_dev_ctrl.server.supervised_sessions import _checkout_blocker

    sm = get_session_manager()
    wt = tmp_path / "wt-parent"
    wt.mkdir()
    parent = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Build feature",
        status=SessionStatus.VERIFYING,
        worktree_mode="isolated",
        worktree_path=str(wt),
        metadata={"supervised": True, "review_mode": "implementation"},
    )
    review = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Review feature",
        status=SessionStatus.RUNNING,
        worktree_mode="reuse",
        worktree_path=str(wt),
        metadata={"supervised": True, "review_mode": "advisory", "recipe_id": "code-review"},
    )

    # Another writer IS blocked by parent, but an advisory reviewer is NOT blocked (and doesn't block writers)
    assert _checkout_blocker("test-project", wt, tmp_path, "other-writer", is_advisory=False) is not None
    assert _checkout_blocker("test-project", wt, tmp_path, review.id, is_advisory=True) is None
    assert parent.id != review.id


def test_archive_and_unarchive_goal_sessions():
    token, _ = get_auth_manager().create_token("archive-api-test", Role.ADMIN)
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)
    sm = get_session_manager()

    run1 = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Declutter completed goals",
        status=SessionStatus.FAILED,
    )
    run2 = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Declutter completed goals",
        status=SessionStatus.COMPLETED,
    )
    running = sm.create_session(
        channel_id="test-channel",
        project="test-project",
        description="Active running goal",
        status=SessionStatus.RUNNING,
    )

    # Cannot archive an actively running goal
    blocked = client.post(f"/sessions/{running.id}/archive", headers=headers)
    assert blocked.status_code == 409

    # Archiving run2 along with sibling run1 marks both with archived_at
    arch = client.post(
        f"/sessions/{run2.id}/archive",
        headers=headers,
        json={"session_ids": [run1.id, run2.id]},
    )
    assert arch.status_code == 200
    body = arch.json()
    assert set(body["archived"]) == {run1.id, run2.id}
    assert body["archived_at"]
    assert body["session"]["archived_at"] == body["archived_at"]

    # GET /sessions/{id} surfaces archived_at
    detail = client.get(f"/sessions/{run1.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["archived_at"] == body["archived_at"]

    # Unarchiving clears archived_at on both runs
    restored = client.post(
        f"/sessions/{run2.id}/unarchive",
        headers=headers,
        json={"session_ids": [run1.id, run2.id]},
    )
    assert restored.status_code == 200
    assert set(restored.json()["restored"]) == {run1.id, run2.id}
    assert restored.json()["session"]["archived_at"] is None
    assert client.get(f"/sessions/{run1.id}", headers=headers).json()["archived_at"] is None


