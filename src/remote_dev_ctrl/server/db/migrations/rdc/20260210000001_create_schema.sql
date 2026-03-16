-- migrate:up

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    config JSON
);

CREATE TABLE IF NOT EXISTS agent_registry (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    provider TEXT DEFAULT 'cursor',
    preferred_worktree TEXT,
    config JSON
);

CREATE TABLE IF NOT EXISTS process_configs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT,
    port INTEGER,
    description TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discovered_by TEXT DEFAULT 'llm',
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS browser_sessions (
    id TEXT PRIMARY KEY,
    process_id TEXT NOT NULL UNIQUE,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    target_url TEXT NOT NULL,
    container_id TEXT,
    container_port INTEGER DEFAULT 0,
    status TEXT DEFAULT 'starting',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP,
    error TEXT
);

CREATE TABLE IF NOT EXISTS contexts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES browser_sessions(id),
    url TEXT,
    title TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    screenshot_path TEXT,
    a11y_path TEXT,
    meta_path TEXT,
    description TEXT DEFAULT '',
    source TEXT DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_process_configs_project_id ON process_configs(project_id);
CREATE INDEX IF NOT EXISTS idx_contexts_project_id ON contexts(project_id);
CREATE INDEX IF NOT EXISTS idx_contexts_session ON contexts(session_id);
CREATE INDEX IF NOT EXISTS idx_contexts_timestamp ON contexts(timestamp);

-- migrate:down

DROP INDEX IF EXISTS idx_contexts_timestamp;
DROP INDEX IF EXISTS idx_contexts_session;
DROP INDEX IF EXISTS idx_contexts_project_id;
DROP INDEX IF EXISTS idx_process_configs_project_id;
DROP TABLE IF EXISTS contexts;
DROP TABLE IF EXISTS browser_sessions;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS process_configs;
DROP TABLE IF EXISTS agent_registry;
DROP TABLE IF EXISTS projects;
