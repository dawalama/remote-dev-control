import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest

from remote_dev_ctrl.server.db.models import Project
from remote_dev_ctrl.server.worktree import WorktreeManager
from remote_dev_ctrl.server.session_manager import get_session_manager, SessionStatus
from remote_dev_ctrl.server.supervised_sessions import create_supervised_session, start_supervised_session
from remote_dev_ctrl.server.terminal import get_terminal_manager
from remote_dev_ctrl.server.db.repositories import get_project_repo


@pytest.fixture
def temp_git_repo(tmp_path):
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True)

    dummy_file = repo_dir / "README.md"
    dummy_file.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


@pytest.fixture
def temp_rdc_home(tmp_path):
    rdc_home = tmp_path / ".rdc"
    rdc_home.mkdir(parents=True, exist_ok=True)
    return rdc_home


def test_worktree_manager_git_detection(temp_git_repo, tmp_path, temp_rdc_home):
    wm = WorktreeManager(rdc_home=temp_rdc_home)
    assert wm.is_git_repo(temp_git_repo) is True

    non_git = tmp_path / "plain_dir"
    non_git.mkdir()
    assert wm.is_git_repo(non_git) is False
    assert wm.list_branches(non_git) == []
    assert wm.get_current_branch(non_git) == ""


def test_worktree_manager_branches(temp_git_repo, temp_rdc_home):
    wm = WorktreeManager(rdc_home=temp_rdc_home)
    current = wm.get_current_branch(temp_git_repo)
    assert current in ("main", "master")

    # Add a second branch
    subprocess.run(["git", "branch", "feature-x"], cwd=temp_git_repo, check=True, capture_output=True)
    branches = wm.list_branches(temp_git_repo)
    assert "feature-x" in branches
    assert current in branches


def test_create_worktree_isolated(temp_git_repo, temp_rdc_home):
    wm = WorktreeManager(rdc_home=temp_rdc_home)
    session_id = "sess-test1"

    wt_path, branch = wm.create_worktree(temp_git_repo, session_id)
    assert wt_path is not None
    assert wt_path.is_dir()
    assert branch == f"rdc/{session_id}"

    # Verify worktree is on branch rdc/sess-test1
    current = wm.get_current_branch(wt_path)
    assert current == f"rdc/{session_id}"

    # Verify worktree is registered in repo
    worktrees = wm.list_worktrees(temp_git_repo)
    paths = [wt["path"] for wt in worktrees]
    assert str(wt_path.resolve()) in [str(Path(p).resolve()) for p in paths]

    # Verify non-destructive cleanup
    cleaned = wm.cleanup_worktree(session_id, worktree_path=wt_path, project_path=temp_git_repo)
    assert cleaned is True
    assert not wt_path.exists()

    # The branch MUST still exist in the repository!
    branches = wm.list_branches(temp_git_repo)
    assert f"rdc/{session_id}" in branches


def test_create_worktree_custom_branch(temp_git_repo, temp_rdc_home):
    wm = WorktreeManager(rdc_home=temp_rdc_home)
    session_id = "sess-test2"

    wt_path, branch = wm.create_worktree(temp_git_repo, session_id, branch_name="my-custom-branch")
    assert wt_path is not None
    assert branch == "my-custom-branch"
    assert wm.get_current_branch(wt_path) == "my-custom-branch"

    wm.cleanup_worktree(session_id, worktree_path=wt_path, project_path=temp_git_repo)
    assert "my-custom-branch" in wm.list_branches(temp_git_repo)


def test_supervised_session_with_worktree_integration(temp_git_repo, monkeypatch):
    # Setup test DB project and channel
    sm = get_session_manager()
    db = sm._db()

    project_name = f"proj-wt-{temp_git_repo.name}"
    proj_repo = get_project_repo()
    proj = proj_repo.create(Project(id="", name=project_name, path=str(temp_git_repo)))

    channel_id = f"ch-wt-{temp_git_repo.name}"
    db.execute(
        "INSERT OR REPLACE INTO channels (id, name, type, collection_id) VALUES (?, ?, 'project', 'general')",
        (channel_id, channel_id),
    )
    db.execute(
        "INSERT OR REPLACE INTO channel_projects (channel_id, project_id) VALUES (?, ?)",
        (channel_id, proj.id),
    )
    db.commit()

    # Create session with isolated worktree (default)
    session = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="Test worktree goal",
        worktree_mode="isolated",
    )
    assert session.worktree_mode == "isolated"

    # Start session
    res = start_supervised_session(session.id)
    assert res["success"] is True

    fresh = sm.get(session.id)
    assert fresh.worktree_path is not None
    assert Path(fresh.worktree_path).is_dir()
    assert fresh.worktree_branch == f"rdc/{session.id}"

    # Check terminal was spawned with worktree as cwd
    tm = get_terminal_manager()
    term = tm.get(fresh.terminal_ids[0])
    assert term is not None
    assert Path(term.cwd).resolve() == Path(fresh.worktree_path).resolve()

    # Create follow-up session: it should automatically reuse parent's worktree!
    followup = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="Followup goal",
        discovered_from=session.id,
    )
    assert followup.worktree_mode == "reuse"
    assert followup.worktree_path == fresh.worktree_path

    # Clean up terminal
    for tid in fresh.terminal_ids:
        tm.destroy(tid)


def test_supervised_session_with_direct_mode(temp_git_repo):
    sm = get_session_manager()
    db = sm._db()

    project_name = f"proj-dir-{temp_git_repo.name}"
    proj_repo = get_project_repo()
    proj = proj_repo.create(Project(id="", name=project_name, path=str(temp_git_repo)))

    channel_id = f"ch-dir-{temp_git_repo.name}"
    db.execute(
        "INSERT OR REPLACE INTO channels (id, name, type, collection_id) VALUES (?, ?, 'project', 'general')",
        (channel_id, channel_id),
    )
    db.execute(
        "INSERT OR REPLACE INTO channel_projects (channel_id, project_id) VALUES (?, ?)",
        (channel_id, proj.id),
    )
    db.commit()

    # Create session with direct mode
    session = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="Test direct goal",
        worktree_mode="direct",
    )
    assert session.worktree_mode == "direct"

    # Start session
    res = start_supervised_session(session.id)
    assert res["success"] is True

    fresh = sm.get(session.id)
    assert fresh.worktree_mode == "direct"
    assert Path(fresh.worktree_path).resolve() == temp_git_repo.resolve()

    # Check terminal was spawned directly in repo
    tm = get_terminal_manager()
    term = tm.get(fresh.terminal_ids[0])
    assert term is not None
    assert Path(term.cwd).resolve() == temp_git_repo.resolve()

    for tid in fresh.terminal_ids:
        tm.destroy(tid)
