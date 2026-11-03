#!/usr/bin/env python3
"""Seed a clean, realistic mock database for RDC documentation and screen recordings.

Usage:
    # Seed into an isolated demo home directory (~/.rdc-demo):
    python scripts/seed_demo_db.py

    # Or specify a custom target directory:
    python scripts/seed_demo_db.py --dir /tmp/my-demo-rdc

    # Start the server against the demo database:
    RDC_HOME=~/.rdc-demo .venv/bin/rdc server start
"""

import argparse
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def seed_demo_database(target_dir: Path):
    print(f"Creating mock demo environment at: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    data_dir = target_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Set environment for initialization
    os.environ["RDC_HOME"] = str(target_dir)

    # Initialize standard schemas using RDC's DB layer
    from remote_dev_ctrl.server.db import init_databases, get_db
    from remote_dev_ctrl.server.channel_manager import get_channel_manager
    from remote_dev_ctrl.server.db.models import (
        Project,
        ProcessConfig,
        ActionKind,
        ProcessStatus,
        ProcessType,
    )
    from remote_dev_ctrl.server.db.repositories import (
        get_project_repo,
        get_process_config_repo,
    )

    init_databases()

    demo_root = Path.home() / "projects"

    # 1. Generic Sample Projects
    project_repo = get_project_repo()
    p1 = project_repo.create(
        Project(
            id="proj-web-app",
            name="web-app",
            path=str(demo_root / "web-app"),
            description="Modern React and TypeScript web application frontend",
        )
    )
    p2 = project_repo.create(
        Project(
            id="proj-api-service",
            name="api-service",
            path=str(demo_root / "api-service"),
            description="FastAPI REST service with PostgreSQL and Redis caching",
        )
    )
    p3 = project_repo.create(
        Project(
            id="proj-worker-service",
            name="worker-service",
            path=str(demo_root / "worker-service"),
            description="Background task worker and asynchronous job processor",
        )
    )

    # 2. Workstream Channels
    cm = get_channel_manager()
    ch1 = cm.ensure_project_channel(p1.id, p1.name)
    ch2 = cm.ensure_project_channel(p2.id, p2.name)
    ch3 = cm.ensure_project_channel(p3.id, p3.name)
    cm.ensure_system_channel()

    # Add initial welcome and discussion messages
    cm.post_message(ch1.id, role="user", content="Look for any bugs and performance issues in the web application, and fix them.")
    cm.post_message(ch1.id, role="agent", content="Starting audit across state management, bundle size, and responsive layout tests.")

    # 3. Actions / Services
    proc_repo = get_process_config_repo()
    proc_repo.upsert(
        ProcessConfig(
            id="web-app-dev",
            project_id=p1.id,
            project=p1.name,
            name="app",
            command="PORT=3000 npm run dev",
            port=3000,
            kind=ActionKind.SERVICE,
            process_type=ProcessType.DEV_SERVER,
            status=ProcessStatus.IDLE,
        )
    )

    proc_repo.upsert(
        ProcessConfig(
            id="web-app-verify",
            project_id=p1.id,
            project=p1.name,
            name="verify",
            command="npm run verify",
            kind=ActionKind.COMMAND,
            process_type=ProcessType.CUSTOM,
            status=ProcessStatus.COMPLETED,
            exit_code=0,
        )
    )

    proc_repo.upsert(
        ProcessConfig(
            id="api-service-server",
            project_id=p2.id,
            project=p2.name,
            name="api",
            command="uv run uvicorn app.main:app --port 8000 --reload",
            port=8000,
            kind=ActionKind.SERVICE,
            process_type=ProcessType.DEV_SERVER,
            status=ProcessStatus.IDLE,
        )
    )

    proc_repo.upsert(
        ProcessConfig(
            id="worker-service-test",
            project_id=p3.id,
            project=p3.name,
            name="test",
            command="pytest tests/ -q",
            kind=ActionKind.COMMAND,
            process_type=ProcessType.CUSTOM,
            status=ProcessStatus.COMPLETED,
            exit_code=0,
        )
    )

    # 4. Supervised Sessions
    rdc_db = get_db("rdc")
    now = datetime.now()

    # Session 1: Ready for Review (web-app)
    sess1_id = "sess-demo-review-01"
    outcome_summary = """`npm run verify` passes: 48 tests, typecheck, production build, and lint.

- Optimized client-side re-renders and virtualized list items.
- Added dark mode color scheme toggle with persistent localStorage preference.
- Fixed layout shift on mobile viewports.
- All ESLint and TypeScript checks pass cleanly.

Preserved existing styling and architecture. Added 12 regression test cases."""

    metadata_sess1 = json.dumps({
        "supervised": True,
        "review_mode": "implementation",
        "requested_provider": "orchestrated",
        "effective_provider": "codex",
    })

    rdc_db.execute("""
        INSERT OR REPLACE INTO sessions
        (id, channel_id, project, terminal_id, task_id, description, status,
         agent_provider, created_at, updated_at, completed_at, output_summary,
         metadata, goal, acceptance_criteria, control_owner, blocker)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sess1_id,
        ch1.id,
        "web-app",
        "term-web-app",
        None,
        "Look for any bugs and performance issues in the web application, and fix them",
        "verifying",
        "orchestrated",
        (now - timedelta(minutes=47)).isoformat(),
        now.isoformat(),
        None,
        outcome_summary,
        metadata_sess1,
        "Look for any bugs and performance issues in the web application, and fix them",
        json.dumps(["npm run verify passes all checks", "No layout shifts on mobile"]),
        "none",
        None,
    ))

    # Evidence for Session 1
    git_diff_stat = """src/App.tsx                                | 18 +++++++--------
 src/components/Dashboard.tsx               | 42 ++++++++++++++++++------------
 src/components/Sidebar.tsx                 | 24 ++++++++---------
 src/hooks/useTheme.ts                      | 36 +++++++++++++++++++++++++
 src/services/apiClient.ts                  | 12 ++++-----
 5 files changed, 81 insertions(+), 51 deletions(-)"""

    rdc_db.execute("""
        INSERT INTO session_evidence (id, session_id, evidence_type, title, data, source, observed_at)
        VALUES (?, ?, 'git', 'Git changes', ?, 'agent', ?)
    """, (
        f"ev-{secrets.token_hex(6)}",
        sess1_id,
        json.dumps({"diff": git_diff_stat, "status": "5 files modified", "head": "a1b2c3d"}),
        (now - timedelta(minutes=5)).isoformat(),
    ))

    rdc_db.execute("""
        INSERT INTO session_evidence (id, session_id, evidence_type, title, data, source, observed_at)
        VALUES (?, ?, 'verification', 'Independent verification', ?, 'verification_runner', ?)
    """, (
        f"ev-{secrets.token_hex(6)}",
        sess1_id,
        json.dumps({
            "command": "npm run verify",
            "exit_code": 0,
            "stdout": "✓ 48 tests passed (0 failed, 48 passed)\n✓ TypeScript compilation clean\n✓ Production build completed (dist/ 245kb)\n✓ ESLint 0 errors, 0 warnings",
        }),
        (now - timedelta(minutes=4)).isoformat(),
    ))

    # Criteria for Session 1
    rdc_db.execute("""
        INSERT INTO session_criteria (session_id, criterion_index, criterion, status, evaluated_at)
        VALUES (?, 0, 'npm run verify passes all checks', 'passed', ?)
    """, (sess1_id, now.isoformat()))
    rdc_db.execute("""
        INSERT INTO session_criteria (session_id, criterion_index, criterion, status, evaluated_at)
        VALUES (?, 1, 'No layout shifts on mobile', 'passed', ?)
    """, (sess1_id, now.isoformat()))

    # Session 2: Needs You / Waiting for Input (api-service)
    sess2_id = "sess-demo-waiting-02"
    rdc_db.execute("""
        INSERT OR REPLACE INTO sessions
        (id, channel_id, project, terminal_id, task_id, description, status,
         agent_provider, created_at, updated_at, output_summary, metadata, goal,
         acceptance_criteria, control_owner, blocker)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sess2_id,
        ch2.id,
        "api-service",
        "term-api-service",
        None,
        "Apply schema migration for user session caching",
        "waiting",
        "claude",
        (now - timedelta(minutes=15)).isoformat(),
        now.isoformat(),
        "Agent is waiting for authorization to execute schema migration on the database.",
        json.dumps({"supervised": True}),
        "Apply schema migration for user session caching",
        json.dumps(["Alembic migration completes cleanly"]),
        "human",
        "Interactive approval requested: alembic upgrade head",
    ))

    rdc_db.execute("""
        INSERT INTO session_approvals (id, session_id, action_type, risk_level, summary, details, status, requested_at)
        VALUES (?, ?, 'command', 'medium', 'Execute database migration on staging schema', ?, 'pending', ?)
    """, (f"appr-{secrets.token_hex(6)}", sess2_id, json.dumps({"command": "alembic upgrade head"}), now.isoformat()))

    # Session 3: Completed (worker-service)
    sess3_id = "sess-demo-done-03"
    rdc_db.execute("""
        INSERT OR REPLACE INTO sessions
        (id, channel_id, project, terminal_id, task_id, description, status,
         agent_provider, created_at, updated_at, completed_at, output_summary,
         metadata, goal, acceptance_criteria, control_owner)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sess3_id,
        ch3.id,
        "worker-service",
        "term-worker-service",
        None,
        "Add pytest unit tests for queue retry backoff",
        "completed",
        "codex",
        (now - timedelta(hours=2)).isoformat(),
        (now - timedelta(hours=1, minutes=45)).isoformat(),
        (now - timedelta(hours=1, minutes=45)).isoformat(),
        "Added 18 unit tests covering exponential backoff, dead letter queue routing, and failure notification handling. All tests passing.",
        json.dumps({"supervised": True}),
        "Add pytest unit tests for queue retry backoff",
        json.dumps(["All tests passing with 100% assertions satisfied"]),
        "none",
    ))

    # Link terminals to sessions
    rdc_db.execute("INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)", (sess1_id, "term-web-app"))
    rdc_db.execute("INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)", (sess2_id, "term-api-service"))
    rdc_db.execute("INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)", (sess3_id, "term-worker-service"))

    rdc_db.commit()
    print("Demo database successfully seeded!")
    print("\nTo launch RDC using this mock database:")
    print(f"    RDC_HOME={target_dir} rdc server start\n")


def main():
    parser = argparse.ArgumentParser(description="Seed clean demo database for RDC")
    parser.add_argument(
        "--dir",
        "-d",
        type=str,
        default=str(Path.home() / ".rdc-demo"),
        help="Target directory for demo RDC_HOME (default: ~/.rdc-demo)",
    )
    args = parser.parse_args()
    seed_demo_database(Path(os.path.expanduser(args.dir)))


if __name__ == "__main__":
    main()
