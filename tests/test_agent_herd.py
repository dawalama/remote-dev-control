"""Tests for the Native Agent Worker Lifecycle ('The Herd') in agent_herd.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from remote_dev_ctrl.server.agent_herd import (
    AGENT_STATUS_IDLE,
    AGENT_STATUS_STOPPED,
    AGENT_STATUS_WORKING,
    get_agent_herd_manager,
)
from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.channel_manager import get_channel_manager
from remote_dev_ctrl.server.conversation import init_conversation_schema
from remote_dev_ctrl.server.db.connection import get_db_manager
from remote_dev_ctrl.server.db.models import ChannelType, Collection, Project
from remote_dev_ctrl.server.db.repositories import get_collection_repo, get_project_repo
from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager
from remote_dev_ctrl.server.supervised_sessions import create_supervised_session, start_supervised_session


@pytest.fixture()
def herd_env(tmp_path: Path):
    db_mgr = get_db_manager()
    herd = get_agent_herd_manager()
    with db_mgr.transaction("rdc") as db:
        herd._ensure_schema(db)
        db.execute("DELETE FROM agent_workers")
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

    coll = c_repo.create(Collection(id="coll-herd", name="HerdTest", role="own"))
    proj_dir = tmp_path / "herd-repo"
    proj_dir.mkdir(parents=True, exist_ok=True)

    proj = p_repo.create(
        Project(
            id="p-herd-1",
            name="herd-app",
            path=str(proj_dir),
            collection_id=coll.id,
        )
    )
    ch = ch_mgr.create_channel(
        name="ch-herd",
        type=ChannelType.PROJECT,
        collection_id=coll.id,
        project_ids=[proj.id],
    )
    return {
        "tmp_path": tmp_path,
        "coll": coll,
        "proj": proj,
        "proj_dir": proj_dir,
        "ch": ch,
        "s_mgr": s_mgr,
        "herd": herd,
    }


def test_agent_lifecycle_spans_multiple_sessions_and_supports_context_reset(herd_env):
    herd = herd_env["herd"]
    s_mgr = herd_env["s_mgr"]
    proj = herd_env["proj"]
    proj_dir = herd_env["proj_dir"]
    ch = herd_env["ch"]

    # 1. Spawn a resident agent on the workstream
    worker = herd.spawn_worker(
        channel_id=ch.id,
        project=proj.name,
        provider="diagnostic",
    )
    assert worker.status == AGENT_STATUS_IDLE
    assert worker.name == "diagnostic-1"
    assert worker.sessions_completed == 0

    # 2. Create and start Session 1 -> binds to our idle resident worker
    s1 = create_supervised_session(
        channel_id=ch.id,
        project=proj.name,
        goal="Goal 1: initial setup",
        provider="diagnostic",
        worktree_mode="direct",
    )
    start_supervised_session(s1.id)

    w_during_s1 = herd.get(worker.id)
    assert w_during_s1.status == AGENT_STATUS_WORKING
    assert w_during_s1.current_session_id == s1.id
    assert s_mgr.get(s1.id).metadata["agent_id"] == worker.id

    # 3. Session 1 finishes into VERIFYING -> Agent Worker does NOT die; it returns to IDLE (warm)!
    s_mgr.update_status(s1.id, SessionStatus.VERIFYING, output_summary="Goal 1 done")
    w_after_s1 = herd.get(worker.id)
    assert w_after_s1.status == AGENT_STATUS_IDLE
    assert w_after_s1.current_session_id is None
    assert w_after_s1.last_session_id == s1.id
    assert w_after_s1.sessions_completed == 1
    assert w_after_s1.turns_completed == 1
    assert herd.should_use_warm_resume(w_after_s1, proj_dir) is True

    # 4. Operator resets agent context before starting an unrelated Goal 2
    w_reset = herd.reset_context(worker.id)
    assert w_reset.context_mode == "fresh"
    assert w_reset.turns_completed == 0
    assert herd.should_use_warm_resume(w_reset, proj_dir) is False

    # Completing Session 1 releases the direct checkout lock
    s_mgr.update_status(s1.id, SessionStatus.COMPLETED)

    # 5. Start Session 2 -> reuses the same Agent Worker, then flips context_mode back to warm on completion
    s2 = create_supervised_session(
        channel_id=ch.id,
        project=proj.name,
        goal="Goal 2: unrelated feature",
        provider="diagnostic",
        worktree_mode="direct",
    )
    start_supervised_session(s2.id)
    assert s_mgr.get(s2.id).metadata["agent_id"] == worker.id

    s_mgr.update_status(s2.id, SessionStatus.VERIFYING, output_summary="Goal 2 done")
    w_after_s2 = herd.get(worker.id)
    assert w_after_s2.status == AGENT_STATUS_IDLE
    assert w_after_s2.context_mode == "warm"
    assert w_after_s2.turns_completed == 1
    assert w_after_s2.sessions_completed == 2


def test_agent_auto_drain_picks_up_staged_session(herd_env):
    herd = herd_env["herd"]
    s_mgr = herd_env["s_mgr"]
    proj = herd_env["proj"]
    ch = herd_env["ch"]

    worker = herd.spawn_worker(
        channel_id=ch.id,
        project=proj.name,
        provider="diagnostic",
        auto_drain=True,
    )

    s1 = create_supervised_session(
        channel_id=ch.id,
        project=proj.name,
        goal="Active goal 1",
        provider="diagnostic",
        worktree_mode="direct",
    )
    s2_staged = create_supervised_session(
        channel_id=ch.id,
        project=proj.name,
        goal="Staged goal 2 waiting in queue",
        provider="diagnostic",
        status="proposed",
        worktree_mode="direct",
    )
    assert s2_staged.status == SessionStatus.PROPOSED

    # Bind worker to s1
    herd.acquire_for_session(s1, effective_provider="diagnostic", preferred_agent_id=worker.id)
    s1.metadata = {**(s1.metadata or {}), "agent_id": worker.id}
    s_mgr._persist(s1)

    assigned_sessions: list[str] = []

    def _fake_assign(agent_id: str, session_id: str):
        assigned_sessions.append(session_id)
        return {"success": True, "session_id": session_id}

    with patch.object(herd, "assign_session", side_effect=_fake_assign):
        s_mgr.update_status(s1.id, SessionStatus.COMPLETED, output_summary="Finished goal 1")

    assert assigned_sessions == [s2_staged.id]


def test_agent_herd_http_endpoints(herd_env):
    ch = herd_env["ch"]
    proj = herd_env["proj"]

    token, _ = get_auth_manager().create_token("herd-api-test", Role.ADMIN)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # Spawn via POST /herd/agents
    resp = client.post(
        "/herd/agents",
        headers=headers,
        json={"channel_id": ch.id, "project": proj.name, "provider": "diagnostic", "auto_drain": False},
    )
    assert resp.status_code == 200
    agent_data = resp.json()
    agent_id = agent_data["id"]
    assert agent_data["status"] == AGENT_STATUS_IDLE

    # Toggle auto_drain via PATCH /herd/agents/{id}
    patch_resp = client.patch(f"/herd/agents/{agent_id}", headers=headers, json={"auto_drain": True, "name": " my-bot "})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["auto_drain"] is True
    assert patch_resp.json()["name"] == "my-bot"

    # Reset context via POST /herd/agents/{id}/reset-context
    reset_resp = client.post(f"/herd/agents/{agent_id}/reset-context", headers=headers)
    assert reset_resp.status_code == 200
    assert reset_resp.json()["context_mode"] == "fresh"

    # Chat with the agent via POST /herd/agents/{id}/chat
    chat_resp = client.post(
        f"/herd/agents/{agent_id}/chat",
        headers=headers,
        json={"message": "Break down the auth refactor into tasks"},
    )
    assert chat_resp.status_code == 200
    assert chat_resp.json()["worker"]["messages"][-1]["role"] == "user"
    assert chat_resp.json()["worker"]["messages"][-1]["content"] == "Break down the auth refactor into tasks"

    # Task board CRUD via /herd/tasks
    t_resp = client.post(
        "/herd/tasks",
        headers=headers,
        json={"channel_id": ch.id, "project": proj.name, "agent_id": agent_id, "title": "Extract token helper"},
    )
    assert t_resp.status_code == 200
    task_id = t_resp.json()["id"]
    assert t_resp.json()["status"] == "todo"

    t_done = client.patch(
        f"/herd/tasks/{task_id}",
        headers=headers,
        json={"status": "done", "result_note": "Extracted and tested"},
    )
    assert t_done.status_code == 200
    assert t_done.json()["status"] == "done"

    t_list = client.get(f"/herd/tasks?channel_id={ch.id}&project={proj.name}", headers=headers)
    assert t_list.status_code == 200
    assert len(t_list.json()) == 1

    # Stop via POST /herd/agents/{id}/stop
    stop_resp = client.post(f"/herd/agents/{agent_id}/stop", headers=headers)
    assert stop_resp.status_code == 200
    assert stop_resp.json()["status"] == AGENT_STATUS_STOPPED

    # Delete via DELETE /herd/agents/{id}
    del_resp = client.delete(f"/herd/agents/{agent_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True


def test_rdc_task_cli_creates_and_completes_board_cards(herd_env):
    import os
    import subprocess
    from remote_dev_ctrl.server.agent_herd import ensure_rdc_task_cli
    from remote_dev_ctrl.server.db.connection import get_db_manager

    herd = herd_env["herd"]
    ch = herd_env["ch"]
    proj = herd_env["proj"]

    worker = herd.spawn_worker(
        channel_id=ch.id,
        project=proj.name,
        provider="diagnostic",
    )
    script_path = ensure_rdc_task_cli()
    assert script_path.exists()

    # Point rdc-task at the test database file
    db_path = herd._active_db_path()
    env = os.environ.copy()
    env["RDC_DB_PATH"] = str(db_path)
    env["RDC_CHANNEL_ID"] = ch.id
    env["RDC_PROJECT"] = proj.name
    env["RDC_AGENT_ID"] = worker.id

    add_res = subprocess.run(
        [str(script_path), "add", "Add rate limiting middleware"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Created task-" in add_res.stdout
    task_id = add_res.stdout.strip().split()[1].rstrip(":")

    subprocess.run([str(script_path), "start", task_id], env=env, text=True, check=True)
    tasks_mid = herd.list_tasks(channel_id=ch.id, project=proj.name)
    assert len(tasks_mid) == 1
    assert tasks_mid[0].status == "in_progress"

    subprocess.run(
        [str(script_path), "done", task_id, "Middleware added with 100 req/min cap"],
        env=env,
        text=True,
        check=True,
    )
    tasks_done = herd.list_tasks(channel_id=ch.id, project=proj.name)
    assert tasks_done[0].status == "done"
    assert tasks_done[0].result_note == "Middleware added with 100 req/min cap"
    assert tasks_done[0].created_by == "agent"

    # Verify `rdc-task pull` claims the oldest Todo task and `rdc-task done "note"` completes it without repeating ID
    t2 = herd.create_task(
        channel_id=ch.id,
        project=proj.name,
        title="Wire rate limiter into API router",
        created_by="user",
    )
    pull_res = subprocess.run(
        [str(script_path), "pull"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"PULLED {t2.id}: Wire rate limiter into API router" in pull_res.stdout
    t2_pulled = next(t for t in herd.list_tasks(channel_id=ch.id, project=proj.name) if t.id == t2.id)
    assert t2_pulled.status == "in_progress"
    assert t2_pulled.agent_id == worker.id

    subprocess.run(
        [str(script_path), "done", "Wired into router and verified"],
        env=env,
        text=True,
        check=True,
    )
    t2_done = next(t for t in herd.list_tasks(channel_id=ch.id, project=proj.name) if t.id == t2.id)
    assert t2_done.status == "done"
    assert t2_done.result_note == "Wired into router and verified"
