-- migrate:up

CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES browser_sessions(id),
    project_id TEXT,
    status TEXT DEFAULT 'recording',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP,
    event_count INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_recordings_session ON recordings(session_id);
CREATE INDEX IF NOT EXISTS idx_recordings_status ON recordings(status);

-- migrate:down

DROP INDEX IF EXISTS idx_recordings_status;
DROP INDEX IF EXISTS idx_recordings_session;
DROP TABLE IF EXISTS recordings;
