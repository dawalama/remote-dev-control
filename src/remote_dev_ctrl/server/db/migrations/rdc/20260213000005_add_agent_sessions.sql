-- migrate:up

CREATE TABLE IF NOT EXISTS agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_session_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    label TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_project ON agent_sessions(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_sessions_unique ON agent_sessions(project_id, agent_session_id);

-- migrate:down

DROP INDEX IF EXISTS idx_agent_sessions_unique;
DROP INDEX IF EXISTS idx_agent_sessions_project;
DROP TABLE IF EXISTS agent_sessions;
