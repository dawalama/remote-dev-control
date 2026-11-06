import os
import shutil
import subprocess
import tempfile
import uuid
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
        provider="diagnostic",
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
        provider="diagnostic",
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
        provider="diagnostic",
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


def _register_project(temp_git_repo: Path, prefix: str):
    sm = get_session_manager()
    db = sm._db()
    suffix = uuid.uuid4().hex[:8]
    project_name = f"{prefix}-{suffix}"
    proj = get_project_repo().create(Project(id="", name=project_name, path=str(temp_git_repo)))
    channel_id = f"ch-{prefix}-{suffix}"
    db.execute(
        "INSERT OR REPLACE INTO channels (id, name, type, collection_id) VALUES (?, ?, 'project', 'general')",
        (channel_id, channel_id),
    )
    db.execute(
        "INSERT OR REPLACE INTO channel_projects (channel_id, project_id) VALUES (?, ?)",
        (channel_id, proj.id),
    )
    db.commit()
    return project_name, channel_id


def _release(session_id: str, project_path: Path) -> None:
    from remote_dev_ctrl.server.worktree import get_worktree_manager

    sm = get_session_manager()
    tm = get_terminal_manager()
    fresh = sm.get(session_id)
    if fresh is None:
        return
    for tid in list(fresh.terminal_ids):
        try:
            tm.destroy(tid)
        except Exception:
            pass
    if fresh.status not in {
        SessionStatus.CANCELLED,
        SessionStatus.FAILED,
        SessionStatus.COMPLETED,
        SessionStatus.DONE,
    }:
        sm.update_status(fresh.id, SessionStatus.CANCELLED)
    if fresh.worktree_path and Path(fresh.worktree_path).resolve() != project_path.resolve():
        get_worktree_manager().cleanup_worktree(
            fresh.id,
            worktree_path=Path(fresh.worktree_path),
            project_path=project_path,
        )


def test_two_isolated_sessions_get_distinct_worktrees(temp_git_repo):
    project_name, channel_id = _register_project(temp_git_repo, "proj-two")
    first = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="First isolated writer",
        provider="diagnostic",
        worktree_mode="isolated",
    )
    second = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="Second isolated writer",
        provider="diagnostic",
        worktree_mode="isolated",
    )
    try:
        assert start_supervised_session(first.id)["success"] is True
        assert start_supervised_session(second.id)["success"] is True
        sm = get_session_manager()
        first_path = Path(sm.get(first.id).worktree_path).resolve()
        second_path = Path(sm.get(second.id).worktree_path).resolve()
        assert first_path != second_path
        assert first_path != temp_git_repo.resolve()
        assert second_path != temp_git_repo.resolve()
        assert first_path.is_dir()
        assert second_path.is_dir()
    finally:
        _release(first.id, temp_git_repo)
        _release(second.id, temp_git_repo)


def test_direct_fallback_refuses_when_checkout_is_held(temp_git_repo, monkeypatch):
    project_name, channel_id = _register_project(temp_git_repo, "proj-held")
    holder = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="Holds the project checkout",
        provider="diagnostic",
        worktree_mode="direct",
    )
    newcomer = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="Would fall back onto the same checkout",
        provider="diagnostic",
        worktree_mode="isolated",
    )
    def _fail_worktree(*_args, **_kwargs):
        raise RuntimeError("worktree failed")

    monkeypatch.setattr(
        "remote_dev_ctrl.server.worktree.WorktreeManager.create_worktree",
        _fail_worktree,
    )
    try:
        assert start_supervised_session(holder.id)["success"] is True
        with pytest.raises(RuntimeError, match=holder.id):
            start_supervised_session(newcomer.id)
        fresh = get_session_manager().get(newcomer.id)
        assert fresh is not None
        assert fresh.status == SessionStatus.FAILED
        assert fresh.worktree_mode != "direct"
        assert fresh.worktree_path != str(temp_git_repo.resolve())
        assert not fresh.terminal_ids
    finally:
        _release(holder.id, temp_git_repo)
        _release(newcomer.id, temp_git_repo)


def test_first_session_falls_back_to_direct_when_worktree_create_fails(temp_git_repo, monkeypatch):
    project_name, channel_id = _register_project(temp_git_repo, "proj-fallback")
    session = create_supervised_session(
        channel_id=channel_id,
        project=project_name,
        goal="First writer may use the project checkout",
        provider="diagnostic",
        worktree_mode="isolated",
    )
    def _fail_worktree(*_args, **_kwargs):
        raise RuntimeError("worktree failed")

    monkeypatch.setattr(
        "remote_dev_ctrl.server.worktree.WorktreeManager.create_worktree",
        _fail_worktree,
    )
    try:
        assert start_supervised_session(session.id)["success"] is True
        fresh = get_session_manager().get(session.id)
        assert fresh.worktree_mode == "direct"
        assert Path(fresh.worktree_path).resolve() == temp_git_repo.resolve()
    finally:
        _release(session.id, temp_git_repo)
