"""Tests for the Autonomous Verify-and-Retry Loop (L1-L3) in session_loop.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_dev_ctrl.server.channel_manager import get_channel_manager
from remote_dev_ctrl.server.config import Config, KnowledgeConfig
from remote_dev_ctrl.server.conversation import init_conversation_schema
from remote_dev_ctrl.server.db.connection import get_db_manager
from remote_dev_ctrl.server.db.models import ChannelType, Collection, Project
from remote_dev_ctrl.server.db.repositories import get_collection_repo, get_project_repo
from remote_dev_ctrl.server.knowledge_bridge import compile_knowledge_claim, get_compiled_knowledge_state
from remote_dev_ctrl.server.session_loop import (
    effective_session_path,
    handle_agent_turn_completed,
    loop_acceptance_block,
    run_verification_sync,
)
from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager


@pytest.fixture()
def loop_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_mgr = get_db_manager()
    k_root = tmp_path / "knowledge"
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(lambda cls, path=None: Config(knowledge=KnowledgeConfig(enabled=True, root=k_root))),
    )

    with db_mgr.transaction("rdc") as db:
        db.execute("DELETE FROM channel_projects")
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM channels WHERE id != 'ch-system'")
        db.execute("DELETE FROM projects")
        db.execute("DELETE FROM collections WHERE id != 'general'")

    c_repo = get_collection_repo()
    p_repo = get_project_repo()
    ch_mgr = get_channel_manager()
    s_mgr = get_session_manager()
    init_conversation_schema()

    coll = c_repo.create(Collection(id="coll-loop", name="LoopTest", role="client"))
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir(parents=True, exist_ok=True)
    wt_dir = tmp_path / "worktree"
    wt_dir.mkdir(parents=True, exist_ok=True)
    (wt_dir / "marker.txt").write_text("in-worktree", encoding="utf-8")

    proj = p_repo.create(
        Project(
            id="p-loop-1",
            name="loop-app",
            path=str(proj_dir),
            collection_id=coll.id,
            config={
                "profile": {
                    "test_command": "python3 -c \"import pathlib, sys; sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)\""
                }
            },
        )
    )
    ch = ch_mgr.create_channel(
        name="ch-loop",
        type=ChannelType.PROJECT,
        collection_id=coll.id,
        project_ids=[proj.id],
    )
    return {
        "tmp_path": tmp_path,
        "coll": coll,
        "proj": proj,
        "proj_dir": proj_dir,
        "wt_dir": wt_dir,
        "ch": ch,
        "s_mgr": s_mgr,
    }


def test_verification_runs_in_session_worktree_path(loop_env):
    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]

    # marker.txt only exists in wt_dir, NOT in proj_dir
    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Verify inside isolated worktree",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
    )

    assert effective_session_path(sess) == wt_dir.resolve()
    res = run_verification_sync(sess)
    assert res["data"]["exit_code"] == 0
    assert res["data"]["cwd"] == str(wt_dir.resolve())


def test_loop_retries_with_feedback_and_proven_patterns_then_succeeds(loop_env):
    s_mgr = loop_env["s_mgr"]
    coll = loop_env["coll"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]

    # Seed a Proven Pattern ("The Line Ahead") in the project's compiled state
    compile_knowledge_claim(
        coll.id,
        statement="Always use _session_effective_path(session) so isolated worktrees are respected.",
        pillar="pattern",
        entity="dawa",
        stance="proven",
        topic="worktree-path-resolution",
        project=proj.name,
    )

    # Script that fails when fail_flag.txt exists, passes when removed
    flag_file = wt_dir / "fail_flag.txt"
    flag_file.write_text("1", encoding="utf-8")

    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Fix worktree path resolution",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "python3 -c \"import pathlib, sys; assert not pathlib.Path('fail_flag.txt').exists(), 'AssertionError: expected worktree path, got root'\"",
            "loop": {"round": 1, "max_rounds": 3},
        },
    )

    captured_instructions: list[str] = []

    def _fake_instruct(session_id: str, instruction: str):
        captured_instructions.append(instruction)
        return {"success": True, "session_id": session_id, "instruction": instruction}

    # Round 1: fails verification -> triggers automatic retry with feedback + proven pattern
    with patch("remote_dev_ctrl.server.session_controls.instruct_session", side_effect=_fake_instruct):
        r1 = asyncio.run(handle_agent_turn_completed(sess, "Initial attempt", agent_exit_code=0))

    assert r1["action"] == "retry"
    assert r1["round"] == 2
    assert len(captured_instructions) == 1
    assert "[RDC Verify Loop — Round 2/3]" in captured_instructions[0]
    assert "AssertionError: expected worktree path, got root" in captured_instructions[0]
    assert "Proven Patterns to follow (The Line Ahead):" in captured_instructions[0]
    assert "_session_effective_path" in captured_instructions[0]

    state_after_r1 = get_compiled_knowledge_state(coll.id, project=proj.name)
    dead_ends = [c for c in state_after_r1["claims"] if c["stance"] == "dead_end"]
    assert dead_ends == []

    # Round 2: agent fixes the issue (removes fail_flag.txt) -> verification passes!
    flag_file.unlink()
    r2 = asyncio.run(handle_agent_turn_completed(sess, "Fixed worktree path resolution", agent_exit_code=0))

    assert r2["action"] == "finish"
    assert r2["end_status"] == SessionStatus.VERIFYING
    assert r2["loop"]["status"] == "passed"
    assert r2["loop"]["exit_reason"] == "success"
    assert r2["loop"]["round"] == 2
    assert "✓ Verified" in r2["summary"]


def test_loop_exits_early_on_stall_when_same_error_repeats(loop_env):
    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]

    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Fix broken import",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "python3 -c \"import sys; sys.stderr.write('FAIL: identical error on line 42\\n'); sys.exit(1)\"",
            "loop": {"round": 1, "max_rounds": 4},
        },
    )

    with patch("remote_dev_ctrl.server.session_controls.instruct_session", return_value={"success": True}):
        r1 = asyncio.run(handle_agent_turn_completed(sess, "Attempt 1", agent_exit_code=0))
    assert r1["action"] == "retry"
    assert r1["round"] == 2

    # Round 2 produces the exact same error signature -> must exit with 'stall' instead of retrying Round 3!
    with patch("remote_dev_ctrl.server.session_controls.instruct_session", return_value={"success": True}) as mock_inst:
        r2 = asyncio.run(handle_agent_turn_completed(sess, "Attempt 2", agent_exit_code=0))
        assert not mock_inst.called

    assert r2["action"] == "finish"
    assert r2["end_status"] == SessionStatus.VERIFYING
    assert r2["loop"]["status"] == "stalled"
    assert r2["loop"]["exit_reason"] == "stall"
    assert r2["loop"]["round"] == 2
    assert "Stalled on round 2/4" in (sess.blocker or "")
    assert loop_acceptance_block(sess) == "verification stall"


def test_loop_budget_exit_is_blocked_from_one_click_accept(loop_env):
    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]
    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Budget one round",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "python3 -c \"import sys; sys.exit(1)\"",
            "loop": {"round": 1, "max_rounds": 1},
        },
    )
    with patch("remote_dev_ctrl.server.session_controls.instruct_session", return_value={"success": True}) as mock_inst:
        result = asyncio.run(handle_agent_turn_completed(sess, "Attempt", agent_exit_code=0))
        assert not mock_inst.called
    assert result["action"] == "finish"
    assert result["end_status"] == SessionStatus.VERIFYING
    assert result["loop"]["status"] == "budget_exhausted"
    assert result["loop"]["exit_reason"] == "budget"
    assert loop_acceptance_block(sess) == "verification budget"


def test_loop_skips_when_worktree_has_no_node_modules(loop_env):
    s_mgr = loop_env["s_mgr"]
    coll = loop_env["coll"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]
    (wt_dir / "package.json").write_text("{}", encoding="utf-8")
    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Needs a runtime",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "npm test",
            "loop": {"round": 1, "max_rounds": 3},
        },
    )
    with patch("remote_dev_ctrl.server.session_controls.instruct_session") as mock_inst:
        result = asyncio.run(handle_agent_turn_completed(sess, "Attempt", agent_exit_code=0))
        assert not mock_inst.called
    assert result["action"] == "finish"
    assert result["end_status"] == SessionStatus.VERIFYING
    assert result["loop"]["status"] == "unverified"
    assert result["loop"]["exit_reason"] == "runtime"
    assert "node_modules" in (sess.blocker or "")
    state = get_compiled_knowledge_state(coll.id, project=proj.name)
    assert [c for c in state["claims"] if c["stance"] == "dead_end"] == []
    assert loop_acceptance_block(sess) == "verification runtime"


def test_verification_scrubs_output_and_passes_only_the_command_criterion(loop_env):
    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]
    command = "python3 -c \"print('sk-' + ('a' * 24))\""
    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Scrub verification output",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        acceptance_criteria=[
            "do not skip verification of auth",
            command,
        ],
        metadata={"supervised": True, "verification_command": command},
    )
    res = run_verification_sync(sess)
    stored = res["data"]["stdout"]
    assert "sk-" not in stored
    assert "[REDACTED]" in stored
    rows = s_mgr._db().execute(
        "SELECT criterion, status FROM session_criteria WHERE session_id = ? ORDER BY criterion_index",
        (sess.id,),
    ).fetchall()
    assert rows[0]["criterion"] == "do not skip verification of auth"
    assert rows[0]["status"] == "unevaluated"
    assert rows[1]["status"] == "passed"


def test_stalled_session_complete_requires_override(loop_env):
    from fastapi.testclient import TestClient

    from remote_dev_ctrl.server.app import app
    from remote_dev_ctrl.server.auth import Role, get_auth_manager

    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Stalled goal",
        metadata={
            "supervised": True,
            "loop": {"round": 2, "max_rounds": 3, "status": "stalled", "exit_reason": "stall"},
        },
    )
    sess.status = SessionStatus.VERIFYING
    s_mgr._persist(sess)
    token, _ = get_auth_manager().create_token("loop-override-test", Role.ADMIN)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    accepted = client.post(f"/sessions/{sess.id}/complete", headers=headers, json={})
    assert accepted.status_code == 200
    completed = s_mgr.get(sess.id)
    assert completed.status == SessionStatus.COMPLETED
    assert any("advisory warnings" in r for r in completed.unresolved_risks)


def test_isolated_worktree_uses_project_root_venv(loop_env):
    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    proj_dir = loop_env["proj_dir"]
    wt_dir = loop_env["wt_dir"]
    ch = loop_env["ch"]

    # Create a .venv/bin executable only in proj_dir (not in wt_dir)
    venv_bin = proj_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    custom_runner = venv_bin / "custom-verify-runner"
    custom_runner.write_text(
        "#!/bin/sh\npython3 -c \"import os, sys; assert 'VIRTUAL_ENV' in os.environ; sys.exit(0)\"\n",
        encoding="utf-8",
    )
    custom_runner.chmod(0o755)

    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Worktree with root .venv",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "custom-verify-runner",
            "loop": {"round": 1, "max_rounds": 3},
        },
    )

    res = asyncio.run(handle_agent_turn_completed(sess, "Done in worktree", agent_exit_code=0))
    assert res["action"] == "finish"
    assert res["loop"]["status"] == "passed"
    assert res["loop"]["exit_reason"] == "success"
    assert loop_acceptance_block(sess) is None


def test_loop_aborts_if_session_cancelled_during_verification(loop_env):
    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]

    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Cancelled mid-verify",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "python3 -c \"import sys; sys.exit(1)\"",
            "loop": {"round": 1, "max_rounds": 3},
        },
    )

    orig_run = run_verification_sync

    def _cancel_while_verifying(session_obj, *args, **kwargs):
        out = orig_run(session_obj, *args, **kwargs)
        s_mgr.update_status(session_obj.id, SessionStatus.CANCELLED)
        return out

    with (
        patch("remote_dev_ctrl.server.session_loop.run_verification_sync", side_effect=_cancel_while_verifying),
        patch("remote_dev_ctrl.server.session_controls.instruct_session") as mock_inst,
    ):
        res = asyncio.run(handle_agent_turn_completed(sess, "Attempt 1", agent_exit_code=0))
        assert not mock_inst.called

    assert res["action"] == "aborted"
    assert s_mgr.get(sess.id).status == SessionStatus.CANCELLED


def test_human_instruction_resets_loop_and_manual_verify_clears_block(loop_env):
    from fastapi.testclient import TestClient

    from remote_dev_ctrl.server.app import app
    from remote_dev_ctrl.server.auth import Role, get_auth_manager
    from remote_dev_ctrl.server.session_controls import instruct_session

    s_mgr = loop_env["s_mgr"]
    proj = loop_env["proj"]
    ch = loop_env["ch"]
    wt_dir = loop_env["wt_dir"]

    sess = s_mgr.create_session(
        channel_id=ch.id,
        project=proj.name,
        description="Stalled then guided",
        agent_provider="diagnostic",
        worktree_mode="isolated",
        worktree_path=str(wt_dir),
        metadata={
            "supervised": True,
            "verification_command": "python3 -c \"import sys; sys.exit(0)\"",
            "loop": {
                "round": 3,
                "max_rounds": 3,
                "status": "budget_exhausted",
                "exit_reason": "budget",
                "history": [{"round": 3, "exit_code": 1, "signature": "sig-abc"}],
            },
        },
    )
    sess.status = SessionStatus.VERIFYING
    sess.blocker = "Verification failed after 3/3 rounds"
    s_mgr._persist(sess)

    # 1. Human follow-up instruction resets round to 1 and clears blocker
    with patch("remote_dev_ctrl.server.supervised_sessions.start_supervised_session", return_value={"success": True}):
        instruct_session(sess.id, "Try fixing the edge case in parser.py")

    reloaded = s_mgr.get(sess.id)
    assert reloaded.metadata["loop"]["round"] == 1
    assert reloaded.metadata["loop"]["status"] == "running"
    assert reloaded.metadata["loop"]["exit_reason"] is None
    assert reloaded.metadata["loop"]["history"] == []
    assert reloaded.blocker is None

    # 2. Manual verification pass via endpoint clears a stalled/runtime loop block
    reloaded.status = SessionStatus.VERIFYING
    reloaded.metadata["loop"] = {"round": 2, "max_rounds": 3, "status": "stalled", "exit_reason": "stall"}
    reloaded.blocker = "Stalled on round 2/3"
    s_mgr._persist(reloaded)
    assert loop_acceptance_block(s_mgr.get(sess.id)) == "verification stall"

    token, _ = get_auth_manager().create_token("manual-verify-test", Role.ADMIN)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    v_resp = client.post(f"/sessions/{sess.id}/evidence/verification", headers=headers, json={})
    assert v_resp.status_code == 200
    assert v_resp.json()["data"]["exit_code"] == 0

    after_verify = s_mgr.get(sess.id)
    assert after_verify.metadata["loop"]["status"] == "passed"
    assert after_verify.metadata["loop"]["exit_reason"] == "success"
    assert after_verify.blocker is None
    assert loop_acceptance_block(after_verify) is None
