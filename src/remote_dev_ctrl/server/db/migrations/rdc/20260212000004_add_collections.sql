-- migrate:up

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO collections (id, name, description, sort_order)
VALUES ('general', 'General', 'Default collection', 0);

ALTER TABLE projects ADD COLUMN collection_id TEXT DEFAULT 'general' REFERENCES collections(id);
UPDATE projects SET collection_id = 'general' WHERE collection_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_projects_collection ON projects(collection_id);

-- migrate:down

DROP INDEX IF EXISTS idx_projects_collection;
