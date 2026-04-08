-- migrate:up
-- Agent sessions: tracks a mission execution with terminal + channel linkage
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    project TEXT NOT NULL,
    terminal_id TEXT,
    task_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, running, waiting, done, failed, cancelled
    agent_provider TEXT,                      -- claude, cursor, gemini, shell, etc.
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    output_summary TEXT,                      -- LLM-generated summary of what happened
    metadata JSON                             -- arbitrary session data
);

CREATE INDEX IF NOT EXISTS idx_sessions_channel ON sessions(channel_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

-- migrate:down
DROP TABLE IF EXISTS sessions;
