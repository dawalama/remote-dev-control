# RDC Data Layer Architecture

## Current: SQLite with dbmate Migrations

All runtime state is stored in SQLite databases managed by [dbmate](https://github.com/amacneil/dbmate) migrations. No file-based state (`.state.json`, `ports.json`, etc.) is used.

### Database Split
```
~/.rdc/data/
├── rdc.db       # Projects, agents, processes, ports, VNC sessions
├── tasks.db     # Task queue + history
└── logs.db      # Time-series events, agent run history
```

### Why Separate DBs
- **rdc.db**: Core state — projects, agent/process configs, port assignments, VNC sessions
- **tasks.db**: Higher writes, can archive old completed tasks
- **logs.db**: Append-heavy, can rotate without affecting core state

### Migration Infrastructure
```
src/remote_dev_ctrl/server/db/
├── connection.py      # get_db(), init_databases(), close_databases()
├── models.py          # Pydantic models for all tables
├── repositories.py    # Repository classes per table
├── migrate.py         # dbmate runner + legacy cleanup
└── migrations/
    ├── rdc/           # rdc.db migrations
    ├── tasks/         # tasks.db migrations
    └── logs/          # logs.db migrations
```

### Repository Pattern

Each table has a dedicated repository class with singleton access:

| Repository | Table | Singleton |
|-----------|-------|-----------|
| `ProjectRepository` | `projects` | `get_project_repo()` |
| `TaskRepository` | `tasks` | `get_task_repo()` |
| `EventRepository` | `events` | `get_event_repo()` |
| `AgentRunRepository` | `agent_runs` | `get_agent_run_repo()` |
| `ProcessConfigRepository` | `process_configs` | `get_process_config_repo()` |
| `AgentStateRepository` | `agent_registry` | `get_agent_state_repo()` |
| `PortAssignmentRepository` | `port_assignments` | `get_port_repo()` |
| `VNCSessionRepository` | `vnc_sessions` | `get_vnc_repo()` |

All repositories follow the same pattern: `upsert()`, `get()`, `list()`, `delete()`, plus table-specific methods.

### Schema Overview

#### rdc.db
```sql
-- Projects registered with RDC
CREATE TABLE projects (
    id TEXT PRIMARY KEY,        -- UUID
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    config JSON
);

-- Agent registry (config + runtime state)
CREATE TABLE agent_registry (
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    provider TEXT DEFAULT 'cursor',
    preferred_worktree TEXT,
    config JSON,
    status TEXT DEFAULT 'idle',
    pid INTEGER,
    current_task TEXT,
    started_at TIMESTAMP,
    last_activity TIMESTAMP,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    worktree TEXT
);

-- Process configurations (config + runtime state)
CREATE TABLE process_configs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT,
    port INTEGER,
    description TEXT,
    process_type TEXT DEFAULT 'dev_server',
    status TEXT DEFAULT 'idle',
    pid INTEGER,
    started_at TIMESTAMP,
    exit_code INTEGER,
    error TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discovered_by TEXT DEFAULT 'llm',
    UNIQUE(project_id, name)
);

-- Port assignments
CREATE TABLE port_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    port INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, service)
);

-- VNC sessions
CREATE TABLE vnc_sessions (
    id TEXT PRIMARY KEY,
    process_id TEXT NOT NULL UNIQUE,
    target_url TEXT NOT NULL,
    vnc_port INTEGER NOT NULL,
    web_port INTEGER NOT NULL,
    container_id TEXT,
    status TEXT DEFAULT 'starting',
    started_at TIMESTAMP,
    error TEXT
);
```

#### tasks.db
```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'pending',
    assigned_to TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result TEXT,
    error TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    metadata JSON,
    depends_on JSON,
    parent_task_id TEXT,
    output TEXT,
    output_artifacts JSON,
    next_tasks JSON,
    requires_review BOOLEAN DEFAULT 0,
    review_prompt TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMP,
    claimed_by TEXT,
    claimed_at TIMESTAMP,
    agent_pid INTEGER,
    agent_log_path TEXT,
    timeout_seconds INTEGER DEFAULT 3600
);
```

#### logs.db
```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    type TEXT NOT NULL,
    project_id TEXT,
    agent TEXT,
    task_id TEXT,
    level TEXT DEFAULT 'info',
    message TEXT,
    data JSON
);

CREATE TABLE agent_runs (
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
);
```

### Foreign Key Convention

All tables reference projects by UUID (`project_id`), not by name. The `resolve_project_id(name_or_uuid)` helper in `repositories.py` converts project names to UUIDs.

### SQLite Patterns for Queue/PubSub

#### Task Queue (no Redis needed)
```python
# Claim next task atomically
UPDATE tasks
SET status = 'in_progress',
    claimed_by = ?,
    claimed_at = CURRENT_TIMESTAMP,
    started_at = CURRENT_TIMESTAMP
WHERE id = (
    SELECT id FROM tasks
    WHERE status = 'pending' AND claimed_by IS NULL
    ORDER BY
        CASE priority
            WHEN 'urgent' THEN 0
            WHEN 'high' THEN 1
            WHEN 'normal' THEN 2
            ELSE 3
        END,
        created_at
    LIMIT 1
)
RETURNING id, project_id, description, priority;
```

### Still on Disk (Not State)
- Config: `~/.rdc/config.yml` (YAML, not state)
- Logs: `~/.rdc/logs/agents/*.log`, `~/.rdc/logs/processes/*.log`
- Event store: `~/.rdc/events/` (DuckDB weekly files)

### Future: Heavy Server Deployment

When running on dedicated server, can optionally upgrade to:
- PostgreSQL + TimescaleDB (same schema, more scale)
- Redis (faster pub/sub, distributed queue)
- ClickHouse or Loki (high-volume logs)

But SQLite should handle:
- Dozens of concurrent agents
- Thousands of tasks/day
- Months of log retention

Only upgrade if hitting limits.
