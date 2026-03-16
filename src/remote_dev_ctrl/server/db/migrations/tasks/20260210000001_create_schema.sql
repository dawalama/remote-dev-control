-- migrate:up

CREATE TABLE IF NOT EXISTS tasks (
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
    output TEXT,
    output_artifacts JSON,
    next_tasks JSON,
    parent_task_id TEXT,
    requires_review BOOLEAN DEFAULT 0,
    review_prompt TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMP,
    claimed_by TEXT,
    claimed_at TIMESTAMP,
    agent_pid INTEGER,
    agent_log_path TEXT,
    timeout_seconds INTEGER DEFAULT 3600,
    context_ids JSON
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_priority_status ON tasks(priority, status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_claimed ON tasks(claimed_by, status);

-- migrate:down

DROP INDEX IF EXISTS idx_tasks_claimed;
DROP INDEX IF EXISTS idx_tasks_created;
DROP INDEX IF EXISTS idx_tasks_priority_status;
DROP INDEX IF EXISTS idx_tasks_project_id;
DROP INDEX IF EXISTS idx_tasks_status;
DROP TABLE IF EXISTS tasks;
