-- migrate:up
ALTER TABLE projects ADD COLUMN tags TEXT DEFAULT '[]';

-- migrate:down
-- SQLite does not support DROP COLUMN before 3.35.0; safe to leave as no-op.
