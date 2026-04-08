-- migrate:up
-- Junction table: sessions can have multiple terminals
CREATE TABLE IF NOT EXISTS session_terminals (
    session_id TEXT NOT NULL,
    terminal_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'agent',  -- agent, shell, monitor
    linked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, terminal_id)
);

CREATE INDEX IF NOT EXISTS idx_session_terminals_session ON session_terminals(session_id);
CREATE INDEX IF NOT EXISTS idx_session_terminals_terminal ON session_terminals(terminal_id);

-- Add index on events.mission_id (used as session_id)
CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id);

-- migrate:down
DROP TABLE IF EXISTS session_terminals;
