-- migrate:up

ALTER TABLE channels ADD COLUMN collection_id TEXT DEFAULT 'general';

-- migrate:down

-- SQLite doesn't support DROP COLUMN before 3.35
