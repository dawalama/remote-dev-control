-- migrate:up

-- Channels: workspaces that contain messages, terminals, and missions
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'project',  -- project, mission, ephemeral, system, event
    parent_channel_id TEXT REFERENCES channels(id) ON DELETE SET NULL,
    auto_mode BOOLEAN DEFAULT FALSE,
    token_spent INTEGER DEFAULT 0,
    token_budget INTEGER,                  -- NULL = unlimited
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP                  -- NULL = active
);

CREATE INDEX IF NOT EXISTS idx_channels_type ON channels(type);
CREATE INDEX IF NOT EXISTS idx_channels_parent ON channels(parent_channel_id);

-- Many-to-many: channels can span multiple projects
CREATE TABLE IF NOT EXISTS channel_projects (
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (channel_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_projects_project ON channel_projects(project_id);

-- Message queue: the conversational history and state log per channel
CREATE TABLE IF NOT EXISTS channel_messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                     -- user, orchestrator, system, agent
    content TEXT,
    metadata JSON,                         -- attachments, tool calls, plan data, etc.
    synced BOOLEAN DEFAULT TRUE,           -- for offline/sync
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_channel_messages_channel ON channel_messages(channel_id, created_at);

-- Terminals can belong to one or more channels
CREATE TABLE IF NOT EXISTS terminal_channels (
    terminal_id TEXT NOT NULL,
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    PRIMARY KEY (terminal_id, channel_id)
);

-- Structured event store: all sources emit events here
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    type TEXT NOT NULL,                    -- e.g. terminal.error_occurred, mission.step_completed
    channel_id TEXT,
    project_id TEXT,
    mission_id TEXT,
    data JSON
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_channel ON events(channel_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- FTS5 virtual table for full-text search across events
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    type, data, content='events', content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS events_fts_insert AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, type, data)
    VALUES (new.rowid, new.type, COALESCE(json_extract(new.data, '$.error'), json_extract(new.data, '$.command'), ''));
END;

-- Seed the #system channel
INSERT OR IGNORE INTO channels (id, name, type) VALUES ('ch-system', '#system', 'system');

-- migrate:down

DROP TRIGGER IF EXISTS events_fts_insert;
DROP TABLE IF EXISTS events_fts;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS terminal_channels;
DROP TABLE IF EXISTS channel_messages;
DROP TABLE IF EXISTS channel_projects;
DROP TABLE IF EXISTS channels;
