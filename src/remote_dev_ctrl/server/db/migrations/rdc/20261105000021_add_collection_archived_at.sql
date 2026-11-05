-- migrate:up

ALTER TABLE collections ADD COLUMN archived_at TIMESTAMP;

-- migrate:down
