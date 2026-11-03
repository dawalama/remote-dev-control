import os
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from remote_dev_ctrl.server.db.models import (
    AgentRun,
    AgentRunStatus,
    AgentState,
    AgentStatus,
    Task,
    TaskPriority,
    TaskStatus,
)
from remote_dev_ctrl.server.db.repositories import (
    get_agent_run_repo,
    get_agent_state_repo,
    get_task_repo,
)
from remote_dev_ctrl.server.session_manager import (
    Session,
    SessionManager,
    SessionStatus,
)


def test_reconcile_orphaned_agent_runs():
    run_repo = get_agent_run_repo()

    # 1. Run with a definitely dead PID
    dead_run = run_repo.create(
        AgentRun(
            project_id="test-proj-dead",
            provider="cursor",
            task="Dead task run",
            pid=99999999,
            status=AgentRunStatus.RUNNING,
        )
    )

    # 2. Run with current process PID (alive)
    alive_run = run_repo.create(
        AgentRun(
            project_id="test-proj-alive",
            provider="cursor",
            task="Alive task run",
            pid=os.getpid(),
            status=AgentRunStatus.RUNNING,
        )
    )

    reconciled_count = run_repo.reconcile_orphaned_runs()
    assert reconciled_count >= 1

    updated_dead = run_repo.get(dead_run.id)
    assert updated_dead is not None
    assert updated_dead.status == AgentRunStatus.FAILED
    assert updated_dead.error == "Process terminated"
    assert updated_dead.ended_at is not None

    updated_alive = run_repo.get(alive_run.id)
    assert updated_alive is not None
    assert updated_alive.status == AgentRunStatus.RUNNING

    # Clean up alive run
    updated_alive.status = AgentRunStatus.COMPLETED
    run_repo.update(updated_alive)


def test_agent_manager_load_states_clears_dead_current_task():
    from remote_dev_ctrl.server.agents.manager import AgentManager
    from remote_dev_ctrl.server.config import Config
    from remote_dev_ctrl.server.db.models import Project
    from remote_dev_ctrl.server.db.repositories import get_project_repo

    project_repo = get_project_repo()
    proj = project_repo.create(
        Project(
            id="proj-dead-agent",
            name="proj-dead-agent",
            path="/tmp/proj-dead-agent",
        )
    )

    state_repo = get_agent_state_repo()
    state_repo.upsert(
        AgentState(
            project_id=proj.id,
            project="proj-dead-agent",
            status=AgentStatus.ERROR,
            provider="cursor",
            current_task="Stale dangling task",
            pid=None,
        )
    )

    config = Config.load()
    am = AgentManager(config)

    agent_state = am.get("proj-dead-agent")
    assert agent_state is not None
    assert agent_state.current_task is None

    # Check that repo was updated
    persisted = state_repo.get(proj.id)
    assert persisted is not None
    assert persisted.current_task is None


def test_task_repo_stats_excludes_failed_from_by_project():
    from remote_dev_ctrl.server.db.models import Project
    from remote_dev_ctrl.server.db.repositories import get_project_repo

    project_repo = get_project_repo()
    proj = project_repo.create(
        Project(
            id="proj-stats-test",
            name="proj-stats-test",
            path="/tmp/proj-stats-test",
        )
    )

    task_repo = get_task_repo()

    # Create one completed and one failed task
    t_failed = task_repo.create(
        project_id=proj.id,
        description="Failed historical task",
    )
    task_repo.db.execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (t_failed.id,))
    task_repo.db.commit()

    t_completed = task_repo.create(
        project_id=proj.id,
        description="Completed historical task",
    )
    task_repo.complete(t_completed.id)

    stats = task_repo.stats()
    assert stats["failed"] >= 1
    # by_project should not list proj-stats-test since both tasks are terminal
    assert proj.name not in stats.get("by_project", {})


def test_session_manager_recover_reconciles_dead_sessions():
    sm = SessionManager()
    db = sm._db()

    session_id = "sess-test-dead-term"
    db.execute(
        """INSERT OR REPLACE INTO sessions
           (id, channel_id, project, terminal_id, task_id, description, status,
            agent_provider, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            "ch-test",
            "test-project",
            "term-nonexistent-12345",
            None,
            "Testing dead session recovery",
            SessionStatus.RUNNING.value,
            "cursor",
            datetime.now().isoformat(),
            datetime.now().isoformat(),
        ),
    )
    db.execute(
        "INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)",
        (session_id, "term-nonexistent-12345"),
    )
    db.commit()

    sm.recover_sessions()

    recovered = sm.get(session_id)
    assert recovered is not None
    assert recovered.status == SessionStatus.FAILED
    assert "terminated" in (recovered.output_summary or "").lower()


def test_orchestrator_updates_agent_run_on_complete():
    from remote_dev_ctrl.server.orchestrator import Orchestrator
    from remote_dev_ctrl.server.config import Config
    from remote_dev_ctrl.server.db.models import Project
    from remote_dev_ctrl.server.db.repositories import get_project_repo

    project_repo = get_project_repo()
    proj = project_repo.create(
        Project(
            id="proj-orch-run-test",
            name="proj-orch-run-test",
            path="/tmp/proj-orch-run-test",
        )
    )

    task_repo = get_task_repo()
    run_repo = get_agent_run_repo()

    task = task_repo.create(
        project_id=proj.id,
        description="Orchestrator test task",
    )

    run = run_repo.create(
        AgentRun(
            project_id=proj.id,
            provider="cursor",
            task=task.description,
            task_id=task.id,
            pid=os.getpid(),
            status=AgentRunStatus.RUNNING,
        )
    )

    config = Config.load()
    am = MagicMock()
    orch = Orchestrator(
        config=config,
        agent_manager=am,
        task_repo=task_repo,
        event_repo=MagicMock(),
    )
    orch._agent_tasks[proj.name] = task.id
    orch._agent_runs[proj.name] = run.id

    orch._on_agent_complete(proj.name, exit_code=0, output="Successfully completed run output")

    updated_run = run_repo.get(run.id)
    assert updated_run is not None
    assert updated_run.status == AgentRunStatus.COMPLETED
    assert updated_run.exit_code == 0
    assert updated_run.ended_at is not None

