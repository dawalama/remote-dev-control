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
import shutil
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

    # 1. Projects
    project_repo = get_project_repo()
    p1 = project_repo.create(
        Project(
            id="proj-tesla-light-show",
            name="tesla-light-show",
            path=str(Path.home() / "PersonalProjects" / "tesla-light-show"),
            description="Tesla interactive light show sequencer & WebAudio visualizer",
        )
    )
    p2 = project_repo.create(
        Project(
            id="proj-mindshare-monitor",
            name="mindshare-monitor",
            path=str(Path.home() / "PersonalProjects" / "mindshare-monitor"),
            description="Social sentiment and keyword mindshare monitoring dashboard",
        )
    )
    p3 = project_repo.create(
        Project(
            id="proj-quickdraw-wallet",
            name="quickdraw-wallet",
            path=str(Path.home() / "PersonalProjects" / "quickdraw-wallet"),
            description="High-frequency automated trading engine for Solana & Base",
        )
    )

    # 2. Workstream Channels
    cm = get_channel_manager()
    ch1 = cm.ensure_project_channel(p1.id, p1.name)
    ch2 = cm.ensure_project_channel(p2.id, p2.name)
    ch3 = cm.ensure_project_channel(p3.id, p3.name)
    cm.ensure_system_channel()

    # Add initial welcome and discussion messages
    cm.post_message(ch1.id, role="user", content="Look for any bugs and performance issues in the sequencer, and fix them.")
    cm.post_message(ch1.id, role="agent", content="Starting audit across audio buffer rendering, timeline ruler slicing, and export dialogs.")

    # 3. Actions / Services
    proc_repo = get_process_config_repo()
    proc_repo.upsert(
        ProcessConfig(
            id="tesla-light-show-app",
            project_id=p1.id,
            project=p1.name,
            name="app",
            command="PORT=5173 npm run dev",
            port=5173,
            kind=ActionKind.SERVICE,
            process_type=ProcessType.DEV_SERVER,
            status=ProcessStatus.IDLE,
        )
    )

    proc_repo.upsert(
        ProcessConfig(
            id="tesla-light-show-verify",
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
            id="mindshare-monitor-api",
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
            id="quickdraw-wallet-test",
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

    # Session 1: Ready for Review (tesla-light-show)
    sess1_id = "sess-demo-review-01"
    outcome_summary = """`npm run verify` passes: 141 tests, typecheck, build, token checks, and lint.

- Exports now include edits still awaiting autosave.
- Extended shows continue playing after the song ends.
- Saved durations and frame rounding stay consistent.
- Timeline ruler rendering is limited to the visible area.
- Bundle imports match audio correctly and skip unrelated files.
- Dialogs retain keyboard focus during playback.
- Server rejects path traversal, malformed JSON, and oversized requests.

Preserved existing edits. Added 43 regression tests."""

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
        "tesla-light-show",
        "term-tesla-light-show",
        None,
        "Look for any bugs and performance issues in the codebase, and fix them",
        "verifying",
        "orchestrated",
        (now - timedelta(minutes=47)).isoformat(),
        now.isoformat(),
        None,
        outcome_summary,
        metadata_sess1,
        "Look for any bugs and performance issues in the codebase, and fix them",
        json.dumps(["npm run verify passes all checks", "Timeline ruler performs at 60fps"]),
        "none",
        None,
    ))

    # Evidence for Session 1
    git_diff_stat = """server/commandPlannerServer.mjs            | 21 +++-------
 src/App.tsx                                |  7 ++--
 src/components/Sequencer/AudioWaveform.tsx | 19 +++++++--
 src/components/Sequencer/TimeRuler.tsx     | 48 +++++++++++++---------
 src/components/Sequencer/TrackGrid.tsx     | 64 ++++++++++++++++++-----------
 src/components/UI/ExportModal.tsx          |  8 +---
 src/hooks/useAutosave.ts                   | 66 ++++++++++++++++++------------
 src/hooks/useShowLibrary.ts                | 58 ++++++++++++++------------
 src/services/audioController.ts            | 50 +++++++++++++++++++---
 src/services/fseqParser.test.ts            | 19 +++++++++
 src/services/fseqParser.ts                 | 14 ++++++-
 src/services/showBundle.ts                 | 43 ++++++++++++++-----
 12 files changed, 275 insertions(+), 142 deletions(-)"""

    rdc_db.execute("""
        INSERT INTO session_evidence (id, session_id, evidence_type, title, data, source, observed_at)
        VALUES (?, ?, 'git', 'Git changes', ?, 'agent', ?)
    """, (
        f"ev-{secrets.token_hex(6)}",
        sess1_id,
        json.dumps({"diff": git_diff_stat, "status": "12 files modified", "head": "404ef4d"}),
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
            "stdout": "✓ 141 tests passed (0 failed, 141 passed)\n✓ Typecheck passed\n✓ Production build completed (dist/ 412kb)\n✓ ESLint clean",
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
        VALUES (?, 1, 'Timeline ruler performs at 60fps', 'passed', ?)
    """, (sess1_id, now.isoformat()))

    # Session 2: Needs You / Waiting for Input (mindshare-monitor)
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
        "mindshare-monitor",
        "term-mindshare-monitor",
        None,
        "Apply schema migration for keyword trend caching",
        "waiting",
        "claude",
        (now - timedelta(minutes=15)).isoformat(),
        now.isoformat(),
        "Agent is waiting for authorization to execute schema migration on the staging database.",
        json.dumps({"supervised": True}),
        "Apply schema migration for keyword trend caching",
        json.dumps(["Alembic migration completes cleanly"]),
        "human",
        "Interactive approval requested: alembic upgrade head",
    ))

    rdc_db.execute("""
        INSERT INTO session_approvals (id, session_id, action_type, risk_level, summary, details, status, requested_at)
        VALUES (?, ?, 'command', 'medium', 'Execute database migration on staging schema', ?, 'pending', ?)
    """, (f"appr-{secrets.token_hex(6)}", sess2_id, json.dumps({"command": "alembic upgrade head"}), now.isoformat()))

    # Session 3: Completed (quickdraw-wallet)
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
        "quickdraw-wallet",
        "term-quickdraw-wallet",
        None,
        "Add pytest unit tests for order routing and signature verification",
        "completed",
        "codex",
        (now - timedelta(hours=2)).isoformat(),
        (now - timedelta(hours=1, minutes=45)).isoformat(),
        (now - timedelta(hours=1, minutes=45)).isoformat(),
        "Added 24 unit tests covering Solana Ed25519 order signature verification and retry backoff. All tests passing.",
        json.dumps({"supervised": True}),
        "Add pytest unit tests for order routing and signature verification",
        json.dumps(["All tests passing with 100% assertions satisfied"]),
        "none",
    ))

    # Link terminals to sessions
    rdc_db.execute("INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)", (sess1_id, "term-tesla-light-show"))
    rdc_db.execute("INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)", (sess2_id, "term-mindshare-monitor"))
    rdc_db.execute("INSERT OR REPLACE INTO session_terminals (session_id, terminal_id) VALUES (?, ?)", (sess3_id, "term-quickdraw-wallet"))

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
