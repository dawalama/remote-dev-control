-- migrate:up
ALTER TABLE process_configs ADD COLUMN kind TEXT DEFAULT 'service';
ALTER TABLE process_configs ADD COLUMN completed_at TIMESTAMP;

-- migrate:down
-- SQLite does not support DROP COLUMN before 3.35; safe to leave as no-op.
