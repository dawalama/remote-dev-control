-- migrate:up

-- Add runtime columns to process_configs
ALTER TABLE process_configs ADD COLUMN process_type TEXT DEFAULT 'dev_server';
ALTER TABLE process_configs ADD COLUMN status TEXT DEFAULT 'idle';
ALTER TABLE process_configs ADD COLUMN pid INTEGER;
ALTER TABLE process_configs ADD COLUMN started_at TIMESTAMP;
ALTER TABLE process_configs ADD COLUMN exit_code INTEGER;
ALTER TABLE process_configs ADD COLUMN error TEXT;

-- Add runtime columns to agent_registry
ALTER TABLE agent_registry ADD COLUMN status TEXT DEFAULT 'idle';
ALTER TABLE agent_registry ADD COLUMN pid INTEGER;
ALTER TABLE agent_registry ADD COLUMN current_task TEXT;
ALTER TABLE agent_registry ADD COLUMN started_at TIMESTAMP;
ALTER TABLE agent_registry ADD COLUMN last_activity TIMESTAMP;
ALTER TABLE agent_registry ADD COLUMN error TEXT;
ALTER TABLE agent_registry ADD COLUMN retry_count INTEGER DEFAULT 0;
ALTER TABLE agent_registry ADD COLUMN worktree TEXT;

-- New table for port assignments
CREATE TABLE IF NOT EXISTS port_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    port INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, service)
);

CREATE INDEX IF NOT EXISTS idx_port_assignments_port ON port_assignments(port);

-- New table for VNC sessions
CREATE TABLE IF NOT EXISTS vnc_sessions (
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

CREATE INDEX IF NOT EXISTS idx_vnc_sessions_status ON vnc_sessions(status);

-- migrate:down

DROP INDEX IF EXISTS idx_vnc_sessions_status;
DROP TABLE IF EXISTS vnc_sessions;
DROP INDEX IF EXISTS idx_port_assignments_port;
DROP TABLE IF EXISTS port_assignments;
