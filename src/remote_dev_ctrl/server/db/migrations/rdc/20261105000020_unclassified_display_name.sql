-- migrate:up
UPDATE collections
SET name = 'Unclassified', updated_at = CURRENT_TIMESTAMP
WHERE id = 'general' AND name = 'General';
-- migrate:down
UPDATE collections
SET name = 'General', updated_at = CURRENT_TIMESTAMP
WHERE id = 'general' AND name = 'Unclassified';
