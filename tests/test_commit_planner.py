"""Tests for diff-aware commit planning and multi-commit execution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager
from remote_dev_ctrl.server.commit_planner import (
    _parse_and_reconcile_plan,
    collect_working_tree_changes,
    generate_commit_plan,
)
from remote_dev_ctrl.server.db.models import Project
from remote_dev_ctrl.server.db.repositories import get_project_repo
from remote_dev_ctrl.server.session_manager import SessionStatus, get_session_manager


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Demo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)


def test_commit_plan_explains_diff_instead_of_echoing_goal(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    (repo / "src" / "server").mkdir(parents=True)
    (repo / "src" / "server" / "archive.py").write_text(
        "def archive_goal(goal_id: str) -> bool:\n    return True\n"
    )

    sm = get_session_manager()
    session = sm.create_session(
        channel_id="test-channel",
        project="demo-proj",
        description="I noticed my screen gets cluttered so please figure out how to let users hide old goals somehow",
        status=SessionStatus.VERIFYING,
        worktree_path=str(repo),
        metadata={"supervised": True, "recipe_id": "feature"},
    )

    plan = generate_commit_plan(session, repo, use_llm=False)
    assert plan["source"] == "heuristic"
    assert len(plan["commits"]) == 1
    msg = plan["commits"][0]["message"]
    assert "cluttered" not in msg.lower()
    assert "figure out" not in msg.lower()
    assert "archive_goal" in msg or "archive" in msg.lower()
    assert plan["commits"][0]["files"] == ["src/server/archive.py"]


def test_parse_and_reconcile_llm_plan_handles_hallucinations_and_trailers(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.tsx").write_text("export const B = () => null\n")
    (repo / "c.md").write_text("notes\n")

    snapshot = collect_working_tree_changes(repo)
    raw = json.dumps({
        "commits": [
            {
                "message": "feat(api): add handler\n\nCo-Authored-By: Bot <bot@example.com>",
                "files": ["a.py", "nonexistent.py"],
                "rationale": "Backend handler",
            },
            {
                "message": "feat(ui): render widget",
                "files": ["b.tsx", "a.py"],
                "rationale": "UI widget",
            },
        ]
    })

    commits = _parse_and_reconcile_plan(raw, snapshot)
    assert commits is not None
    assert len(commits) == 2
    assert "Co-Authored-By" not in commits[0]["message"]
    assert commits[0]["files"] == ["a.py"]
    # Duplicate a.py dropped, hallucinated nonexistent.py dropped, omitted c.md reconciled
    assert commits[1]["files"] == ["b.tsx", "c.md"]


def test_multi_commit_plan_and_execution_endpoints(tmp_path):
    repo = tmp_path / "multi-repo"
    _init_git_repo(repo)

    # Create changes spanning backend, frontend, tests, and docs
    (repo / "src" / "server").mkdir(parents=True)
    (repo / "src" / "server" / "billing.py").write_text("def compute_invoice(amount: int) -> int:\n    return amount\n")
    (repo / "frontend" / "src").mkdir(parents=True)
    (repo / "frontend" / "src" / "InvoiceCard.tsx").write_text("export function InvoiceCard() { return null }\n")
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_billing.py").write_text("def test_invoice():\n    assert True\n")
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "billing.md").write_text("# Billing\n")

    proj_repo = get_project_repo()
    proj_name = f"commit-plan-test-{tmp_path.name}"
    proj_repo.create(Project(name=proj_name, path=str(repo)))

    sm = get_session_manager()
    session = sm.create_session(
        channel_id="test-channel",
        project=proj_name,
        description="Build billing feature with UI, tests, and docs",
        status=SessionStatus.VERIFYING,
        worktree_path=str(repo),
        metadata={"supervised": True, "recipe_id": "feature"},
    )

    token, _ = get_auth_manager().create_token("commit-plan-api-test", Role.ADMIN)
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)

    # 1. Plan logical commits
    plan_resp = client.post(
        f"/sessions/{session.id}/git-commit-plan",
        headers=headers,
        json={"use_llm": False},
    )
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert plan["cached"] is False
    assert len(plan["commits"]) >= 3
    all_planned_files = [fp for c in plan["commits"] for fp in c["files"]]
    assert set(all_planned_files) == {
        "src/server/billing.py",
        "frontend/src/InvoiceCard.tsx",
        "tests/test_billing.py",
        "docs/billing.md",
    }
    assert len(all_planned_files) == 4  # no duplicates

    # 2. Cached response on immediate re-request
    cached_resp = client.post(
        f"/sessions/{session.id}/git-commit-plan",
        headers=headers,
        json={"use_llm": False},
    )
    assert cached_resp.status_code == 200
    assert cached_resp.json()["cached"] is True

    # 3. Execute the multi-commit plan
    commit_resp = client.post(
        f"/sessions/{session.id}/git-commit",
        headers=headers,
        json={
            "commits": [
                {"message": c["message"], "files": c["files"]}
                for c in plan["commits"]
            ],
            "push": False,
        },
    )
    assert commit_resp.status_code == 200
    commit_data = commit_resp.json()
    assert len(commit_data["commits"]) == len(plan["commits"])

    # Verify actual git history has each separate logical commit and clean working tree
    log_out = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    # Initial commit + all planned commits
    assert len(log_out) == 1 + len(plan["commits"])
    assert log_out[0] == plan["commits"][-1]["message"]

    status_out = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert status_out == ""
