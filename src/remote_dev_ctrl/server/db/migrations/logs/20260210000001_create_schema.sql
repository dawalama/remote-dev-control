-- migrate:up

CREATE TABLE IF NOT EXISTS events (
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

CREATE TABLE IF NOT EXISTS agent_runs (
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

CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    pid INTEGER NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'running',
    max_concurrent INTEGER DEFAULT 3,
    current_load INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_project_id ON events(project_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_runs_project_id ON agent_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_time ON agent_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat);

-- migrate:down

DROP INDEX IF EXISTS idx_workers_heartbeat;
DROP INDEX IF EXISTS idx_workers_status;
DROP INDEX IF EXISTS idx_runs_status;
DROP INDEX IF EXISTS idx_runs_time;
DROP INDEX IF EXISTS idx_runs_project_id;
DROP INDEX IF EXISTS idx_events_type;
DROP INDEX IF EXISTS idx_events_project_id;
DROP INDEX IF EXISTS idx_events_time;
DROP TABLE IF EXISTS workers;
DROP TABLE IF EXISTS agent_runs;
DROP TABLE IF EXISTS events;
