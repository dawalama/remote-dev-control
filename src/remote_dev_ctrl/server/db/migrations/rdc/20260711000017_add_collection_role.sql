-- migrate:up

ALTER TABLE collections ADD COLUMN role TEXT NOT NULL DEFAULT 'unsorted';
UPDATE collections SET role = 'unsorted' WHERE id = 'general';

-- migrate:down
