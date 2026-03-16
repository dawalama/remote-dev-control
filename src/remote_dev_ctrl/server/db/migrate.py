"""Database migration and legacy data merge utilities.

Handles:
- Running dbmate migrations against rdc.db, tasks.db, logs.db
- One-time migration of legacy main.db / adt.db → rdc.db (copy, not destructive)
- One-time migration of legacy project columns (name → UUID project_id)
"""

import logging
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

from ..config import get_rdc_home

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Databases and their migration subdirectories
DATABASES = {
    "rdc": "rdc",
    "tasks": "tasks",
    "logs": "logs",
}


def _db_path(db_name: str) -> Path:
    """Get the path to a database file, with compat fallback for rdc→adt."""
    data_dir = get_rdc_home() / "data"
    path = data_dir / f"{db_name}.db"
    # Compat: if rdc.db doesn't exist but adt.db does, use adt.db
    if db_name == "rdc" and not path.exists():
        legacy = data_dir / "adt.db"
        if legacy.exists():
            return legacy
    return path


def run_dbmate(db_name: str):
    """Run dbmate migrations for a specific database."""
    db_path = _db_path(db_name)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    migrations_subdir = MIGRATIONS_DIR / DATABASES[db_name]
    if not migrations_subdir.exists():
        logger.warning(f"No migrations directory for {db_name}: {migrations_subdir}")
        return

    db_url = f"sqlite:{db_path}"
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url

    try:
        result = subprocess.run(
            [
                "dbmate",
                "--migrations-dir", str(migrations_subdir),
                "--no-dump-schema",
                "up",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "dbmate not found. Install it: brew install dbmate (macOS) "
            "or see https://github.com/amacneil/dbmate#installation"
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # "already exists" errors are fine for IF NOT EXISTS migrations
        if "already exists" not in stderr.lower():
            logger.error(f"dbmate migration failed for {db_name}: {stderr}")
            raise RuntimeError(f"dbmate failed for {db_name}: {stderr}")

    if result.stdout.strip():
        logger.info(f"dbmate [{db_name}]: {result.stdout.strip()}")


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a table has a specific column."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def _build_project_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Build a project name → UUID mapping from the projects table.

    If projects already have an 'id' column, use existing values.
    Otherwise, generate new UUIDs for each project.
    """
    project_map: dict[str, str] = {}

    if not _table_exists(conn, "projects"):
        return project_map

    has_id = _has_column(conn, "projects", "id")

    if has_id:
        # Projects already migrated — just build the map
        rows = conn.execute("SELECT id, name FROM projects").fetchall()
        for row in rows:
            project_map[row[0] if isinstance(row, (list, tuple)) else row["name"]] = (
                row[1] if isinstance(row, (list, tuple)) else row["id"]
            )
            # Actually: row is (id, name) → map name→id
        # Redo: sqlite3 default row_factory returns tuples
        for row in conn.execute("SELECT id, name FROM projects").fetchall():
            pid, name = row[0], row[1]
            project_map[name] = pid
    else:
        # Need to add id column and generate UUIDs
        rows = conn.execute("SELECT name FROM projects").fetchall()
        for row in rows:
            name = row[0]
            project_map[name] = str(uuid.uuid4())

    return project_map


def _ensure_project_in_map(
    conn: sqlite3.Connection,
    project_map: dict[str, str],
    name: str,
) -> str:
    """Ensure a project name has a UUID in the map.

    Creates an ad-hoc project entry if needed for orphan references.
    """
    if name in project_map:
        return project_map[name]

    pid = str(uuid.uuid4())
    project_map[name] = pid
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, path, created_at, updated_at) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (pid, name, f"/unknown/{name}"),
    )
    logger.warning(f"Created ad-hoc project for orphan reference: {name} → {pid}")
    return pid


def migrate_legacy_adt_db():
    """Migrate legacy main.db → rdc.db with UUID project ids.

    Only handles the ancient main.db → rdc.db migration (pre-UUID era).
    If adt.db exists but rdc.db doesn't, we do NOT copy — the connection
    shim in connection.py uses adt.db directly, and dbmate runs migrations
    against it in place. This avoids creating a stale copy that misses
    newer schema changes.
    """
    data_dir = get_rdc_home() / "data"
    old_main = data_dir / "main.db"
    new_rdc = data_dir / "rdc.db"
    old_adt = data_dir / "adt.db"

    # If rdc.db or adt.db already exist, no main.db migration needed
    if new_rdc.exists() or old_adt.exists():
        return

    if not old_main.exists():
        return  # No legacy DB — fresh install

    logger.info(f"Legacy main.db detected. Copying to rdc.db for migration...")
    shutil.copy2(old_main, new_rdc)

    conn = sqlite3.connect(new_rdc)
    conn.execute("PRAGMA foreign_keys = OFF")

    try:
        # Build project name → UUID map
        project_map = _build_project_map(conn)

        # Add id column to projects if missing
        if not _has_column(conn, "projects", "id"):
            conn.execute("ALTER TABLE projects ADD COLUMN id TEXT")
            for name, pid in project_map.items():
                conn.execute(
                    "UPDATE projects SET id = ? WHERE name = ?", (pid, name)
                )

        # --- Migrate agent_registry: project → project_id ---
        if _table_exists(conn, "agent_registry") and _has_column(conn, "agent_registry", "project"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_registry_new (
                    project_id TEXT PRIMARY KEY,
                    provider TEXT DEFAULT 'cursor',
                    preferred_worktree TEXT,
                    config JSON
                )
            """)
            for row in conn.execute("SELECT * FROM agent_registry").fetchall():
                name = row[0]  # project is PK (first column)
                pid = _ensure_project_in_map(conn, project_map, name)
                conn.execute(
                    "INSERT OR IGNORE INTO agent_registry_new (project_id, provider, preferred_worktree, config) "
                    "VALUES (?, ?, ?, ?)",
                    (pid, row[1], row[2], row[3]),
                )
            conn.execute("DROP TABLE agent_registry")
            conn.execute("ALTER TABLE agent_registry_new RENAME TO agent_registry")

        # --- Migrate process_configs: project → project_id ---
        if _table_exists(conn, "process_configs") and _has_column(conn, "process_configs", "project"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_configs_new (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    command TEXT NOT NULL,
                    cwd TEXT,
                    port INTEGER,
                    description TEXT,
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    discovered_by TEXT DEFAULT 'llm',
                    UNIQUE(project_id, name)
                )
            """)
            for row in conn.execute("SELECT * FROM process_configs").fetchall():
                row_id, project_name = row[0], row[1]
                pid = _ensure_project_in_map(conn, project_map, project_name)
                conn.execute(
                    "INSERT OR IGNORE INTO process_configs_new "
                    "(id, project_id, name, command, cwd, port, description, discovered_at, discovered_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row_id, pid, row[2], row[3], row[4], row[5], row[6], row[7], row[8]),
                )
            conn.execute("DROP TABLE process_configs")
            conn.execute("ALTER TABLE process_configs_new RENAME TO process_configs")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_process_configs_project_id ON process_configs(project_id)")

        # --- Migrate browser_sessions: add project_id ---
        if _table_exists(conn, "browser_sessions") and not _has_column(conn, "browser_sessions", "project_id"):
            conn.execute("ALTER TABLE browser_sessions ADD COLUMN project_id TEXT")

        # --- Migrate contexts: project → project_id ---
        if _table_exists(conn, "contexts") and _has_column(conn, "contexts", "project"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contexts_new (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT,
                    url TEXT,
                    title TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    screenshot_path TEXT,
                    a11y_path TEXT,
                    meta_path TEXT,
                    description TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual'
                )
            """)
            for row in conn.execute("SELECT * FROM contexts").fetchall():
                ctx_id = row[0]
                project_name = row[1]
                pid = _ensure_project_in_map(conn, project_map, project_name)
                conn.execute(
                    "INSERT OR IGNORE INTO contexts_new "
                    "(id, project_id, session_id, url, title, timestamp, screenshot_path, a11y_path, meta_path, description, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ctx_id, pid, row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10]),
                )
            conn.execute("DROP TABLE contexts")
            conn.execute("ALTER TABLE contexts_new RENAME TO contexts")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_project_id ON contexts(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_session ON contexts(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_timestamp ON contexts(timestamp)")

        # Now rebuild projects table with id as PK
        if _table_exists(conn, "projects"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects_new (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    config JSON
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO projects_new (id, name, path, created_at, updated_at, config)
                SELECT id, name, path, created_at, updated_at, config FROM projects
            """)
            conn.execute("DROP TABLE projects")
            conn.execute("ALTER TABLE projects_new RENAME TO projects")

        conn.commit()
        logger.info(f"Legacy rdc.db migration complete. {len(project_map)} projects assigned UUIDs.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_legacy_tasks_db(project_map: dict[str, str]):
    """Migrate tasks.db: rename project column to project_id, populate UUIDs.

    Backs up tasks.db → tasks.db.bak before modifying.
    """
    db_path = _db_path("tasks")
    if not db_path.exists():
        return

    conn = sqlite3.connect(db_path)
    if not _table_exists(conn, "tasks") or not _has_column(conn, "tasks", "project"):
        conn.close()
        return  # Already migrated or no tasks table

    logger.info("Migrating tasks.db: project → project_id...")

    # Backup first
    backup = db_path.with_suffix(".db.bak")
    if not backup.exists():
        shutil.copy2(db_path, backup)
        logger.info(f"Backed up tasks.db → {backup.name}")

    try:
        # Get all column info to rebuild the table
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = [(row[1], row[2], row[4]) for row in cursor.fetchall()]  # name, type, default

        # Build new column list (replace 'project' with 'project_id')
        new_cols = []
        old_col_names = []
        for col_name, col_type, col_default in columns:
            old_col_names.append(col_name)
            if col_name == "project":
                new_cols.append(("project_id", col_type, col_default))
            else:
                new_cols.append((col_name, col_type, col_default))

        # Create new table
        col_defs = []
        for col_name, col_type, col_default in new_cols:
            definition = f"{col_name} {col_type}"
            if col_name == "id":
                definition += " PRIMARY KEY"
            elif col_name == "project_id":
                definition += " NOT NULL"
            if col_default is not None and col_name not in ("id", "project_id"):
                definition += f" DEFAULT {col_default}"
            col_defs.append(definition)

        conn.execute(f"CREATE TABLE tasks_new ({', '.join(col_defs)})")

        # Copy data, mapping project name → UUID
        rows = conn.execute("SELECT * FROM tasks").fetchall()
        for row in rows:
            values = list(row)
            # Find project column index
            proj_idx = old_col_names.index("project")
            project_name = values[proj_idx]
            if project_name in project_map:
                values[proj_idx] = project_map[project_name]
            # If not in map, keep original value (will be a name, not UUID — best effort)

            placeholders = ", ".join(["?"] * len(values))
            new_col_names = [c[0] for c in new_cols]
            conn.execute(
                f"INSERT OR IGNORE INTO tasks_new ({', '.join(new_col_names)}) VALUES ({placeholders})",
                values,
            )

        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_new RENAME TO tasks")

        # Recreate indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority_status ON tasks(priority, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_claimed ON tasks(claimed_by, status)")

        conn.commit()
        logger.info("tasks.db migration complete.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_legacy_logs_db(project_map: dict[str, str]):
    """Migrate logs.db: rename project columns to project_id, populate UUIDs.

    Backs up logs.db → logs.db.bak before modifying.
    """
    db_path = _db_path("logs")
    if not db_path.exists():
        return

    conn = sqlite3.connect(db_path)

    needs_migration = (
        (_table_exists(conn, "events") and _has_column(conn, "events", "project"))
        or (_table_exists(conn, "agent_runs") and _has_column(conn, "agent_runs", "project"))
    )
    if not needs_migration:
        conn.close()
        return

    logger.info("Migrating logs.db: project → project_id...")

    # Backup first
    backup = db_path.with_suffix(".db.bak")
    if not backup.exists():
        shutil.copy2(db_path, backup)
        logger.info(f"Backed up logs.db → {backup.name}")

    try:
        # --- Migrate events ---
        if _table_exists(conn, "events") and _has_column(conn, "events", "project"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    type TEXT NOT NULL,
                    project_id TEXT,
                    agent TEXT,
                    task_id TEXT,
                    level TEXT DEFAULT 'info',
                    message TEXT,
                    data JSON
                )
            """)
            for row in conn.execute("SELECT * FROM events").fetchall():
                project_name = row[3]  # project is 4th column
                project_id = project_map.get(project_name) if project_name else None
                conn.execute(
                    "INSERT INTO events_new (timestamp, type, project_id, agent, task_id, level, message, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (row[1], row[2], project_id, row[4], row[5], row[6], row[7], row[8]),
                )
            conn.execute("DROP TABLE events")
            conn.execute("ALTER TABLE events_new RENAME TO events")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project_id ON events(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")

        # --- Migrate agent_runs ---
        if _table_exists(conn, "agent_runs") and _has_column(conn, "agent_runs", "project"):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    provider TEXT,
                    task TEXT,
                    task_id TEXT,
                    pid INTEGER,
                    started_at TIMESTAMP NOT NULL,
                    ended_at TIMESTAMP,
                    exit_code INTEGER,
                    status TEXT,
                    error TEXT,
                    log_file TEXT
                )
            """)
            for row in conn.execute("SELECT * FROM agent_runs").fetchall():
                project_name = row[1]  # project is 2nd column
                project_id = project_map.get(project_name, project_name)  # fallback to name
                conn.execute(
                    "INSERT INTO agent_runs_new "
                    "(project_id, provider, task, task_id, pid, started_at, ended_at, exit_code, status, error, log_file) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11]),
                )
            conn.execute("DROP TABLE agent_runs")
            conn.execute("ALTER TABLE agent_runs_new RENAME TO agent_runs")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project_id ON agent_runs(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_time ON agent_runs(started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON agent_runs(status)")

        conn.commit()
        logger.info("logs.db migration complete.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _load_project_map_from_adt() -> dict[str, str]:
    """Load project name → UUID mapping from rdc.db (after it's been migrated)."""
    db_path = _db_path("rdc")
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(db_path)
    project_map: dict[str, str] = {}

    try:
        if _table_exists(conn, "projects") and _has_column(conn, "projects", "id"):
            for row in conn.execute("SELECT id, name FROM projects").fetchall():
                project_map[row[1]] = row[0]  # name → id
    finally:
        conn.close()

    return project_map


def cleanup_legacy_state_files():
    """Remove legacy file-based state directories.

    These are no longer needed since all state is in SQLite:
    - ~/.rdc/processes/  (*.state.json)
    - ~/.rdc/agents/     (*.state.json)
    - ~/.rdc/vnc/        (*.state.json)
    - ~/.rdc/queue/      (tasks.json)
    - ~/.rdc/ports.json
    """
    home = get_rdc_home()
    legacy_dirs = ["processes", "agents", "vnc", "queue"]
    legacy_files = ["ports.json"]

    for dirname in legacy_dirs:
        dirpath = home / dirname
        if dirpath.exists():
            try:
                shutil.rmtree(dirpath)
                logger.info(f"Removed legacy state directory: {dirpath}")
            except Exception as e:
                logger.warning(f"Failed to remove {dirpath}: {e}")

    for filename in legacy_files:
        filepath = home / filename
        if filepath.exists():
            try:
                filepath.unlink()
                logger.info(f"Removed legacy state file: {filepath}")
            except Exception as e:
                logger.warning(f"Failed to remove {filepath}: {e}")


def migrate_yaml_projects_to_db():
    """One-time migration: copy projects from YAML config.json into SQLite.

    Reads from ~/.config/remote-dev-ctrl/config.json (or legacy agent-dev-tool)
    and inserts any projects not already present in the DB (matched by path).
    """
    try:
        from ...store import load_config as load_yaml_config
    except Exception:
        return  # store module not available

    try:
        yaml_cfg = load_yaml_config()
    except Exception:
        return  # No config file or parse error

    if not yaml_cfg.projects:
        return

    from .repositories import ProjectRepository
    from .models import Project

    repo = ProjectRepository()
    db_projects = repo.list()
    db_paths = {p.path for p in db_projects}
    db_names = {p.name for p in db_projects}

    migrated = 0
    for yp in yaml_cfg.projects:
        ypath = str(yp.path)
        if ypath in db_paths or yp.name in db_names:
            continue
        repo.create(Project(
            name=yp.name,
            path=ypath,
            description=yp.description or "",
            tags=yp.tags if yp.tags else [],
        ))
        migrated += 1

    if migrated:
        logger.info(f"Migrated {migrated} project(s) from YAML config to SQLite.")


def ensure_database():
    """Main entry point: migrate legacy data if needed, then run dbmate.

    Call this on startup instead of the old init_databases() inline SQL.
    """
    data_dir = get_rdc_home() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Migrate legacy main.db → adt.db (copy, not destructive)
    migrate_legacy_adt_db()

    # Step 2: Run dbmate on rdc.db first (creates schema if fresh install)
    run_dbmate("rdc")

    # Step 3: Load project map from adt.db for cross-DB migrations
    project_map = _load_project_map_from_adt()

    # Step 4: Migrate tasks.db and logs.db (backup + transform)
    migrate_legacy_tasks_db(project_map)
    migrate_legacy_logs_db(project_map)

    # Step 5: Run dbmate on tasks.db and logs.db
    run_dbmate("tasks")
    run_dbmate("logs")

    # Step 6: Clean up legacy state files
    cleanup_legacy_state_files()

    # Step 7: Migrate YAML projects to SQLite (one-time)
    migrate_yaml_projects_to_db()

    logger.info("Database initialization complete.")
