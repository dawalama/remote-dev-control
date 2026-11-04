-- migrate:up

ALTER TABLE collections ADD COLUMN default_agent_id TEXT;

-- migrate:down
