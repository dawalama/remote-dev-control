-- migrate:up

-- Clean up from any partial previous run
DROP TABLE IF EXISTS browser_sessions_new;

-- SQLite can't ALTER NOT NULL, so rebuild browser_sessions with nullable process_id
-- target_url defaults to '' to handle legacy rows with NULL values
CREATE TABLE browser_sessions_new (
    id TEXT PRIMARY KEY,
    process_id TEXT,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    target_url TEXT DEFAULT '',
    container_id TEXT,
    container_port INTEGER DEFAULT 0,
    status TEXT DEFAULT 'starting',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP,
    error TEXT
);

INSERT INTO browser_sessions_new (id, process_id, project_id, target_url, container_id, container_port, status, created_at, stopped_at, error)
    SELECT id, process_id, project_id, COALESCE(target_url, ''), container_id, container_port, status, created_at, stopped_at, error
    FROM browser_sessions;
DROP TABLE browser_sessions;
ALTER TABLE browser_sessions_new RENAME TO browser_sessions;

-- Re-create index (contexts FK is soft in SQLite so no rebuild needed)
CREATE INDEX IF NOT EXISTS idx_browser_sessions_process ON browser_sessions(process_id);

-- migrate:down

-- Reverse: rebuild with NOT NULL + UNIQUE constraint
CREATE TABLE IF NOT EXISTS browser_sessions_old (
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

INSERT INTO browser_sessions_old SELECT * FROM browser_sessions WHERE process_id IS NOT NULL;
DROP TABLE browser_sessions;
ALTER TABLE browser_sessions_old RENAME TO browser_sessions;
