-- migrate:up
ALTER TABLE sessions ADD COLUMN worktree_mode TEXT NOT NULL DEFAULT 'isolated';
ALTER TABLE sessions ADD COLUMN worktree_path TEXT;
ALTER TABLE sessions ADD COLUMN worktree_branch TEXT;
ALTER TABLE sessions ADD COLUMN discovered_from TEXT REFERENCES sessions(id) ON DELETE SET NULL;
ALTER TABLE sessions ADD COLUMN handoff_brief TEXT;

CREATE INDEX IF NOT EXISTS idx_sessions_discovered_from ON sessions(discovered_from);

-- migrate:down
