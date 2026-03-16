-- migrate:up

ALTER TABLE projects ADD COLUMN description TEXT;

-- migrate:down

-- SQLite doesn't support DROP COLUMN before 3.35.0; safe to leave as no-op
