-- migrate:up
-- Email threads: bidirectional email conversations with the orchestrator
CREATE TABLE IF NOT EXISTS email_threads (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT '',
    from_address TEXT NOT NULL,
    project_id TEXT,                           -- resolved project (nullable until routed)
    status TEXT NOT NULL DEFAULT 'open',       -- open, ongoing, waiting, closed
    condensed_context TEXT,                    -- LLM-generated summary of thread so far
    tags JSON DEFAULT '[]',                    -- user-applied tags from subject/body
    task_ids JSON DEFAULT '[]',               -- linked task IDs created from this thread
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    metadata JSON                              -- extra context (e.g. routing confidence)
);

CREATE INDEX IF NOT EXISTS idx_email_threads_status ON email_threads(status);
CREATE INDEX IF NOT EXISTS idx_email_threads_project ON email_threads(project_id);
CREATE INDEX IF NOT EXISTS idx_email_threads_from ON email_threads(from_address);
CREATE INDEX IF NOT EXISTS idx_email_threads_updated ON email_threads(updated_at DESC);

-- Individual email messages within a thread
CREATE TABLE IF NOT EXISTS email_messages (
    id TEXT PRIMARY KEY,                       -- internal ID
    thread_id TEXT NOT NULL REFERENCES email_threads(id),
    message_id TEXT NOT NULL,                  -- RFC 822 Message-ID header
    in_reply_to TEXT,                          -- Message-ID this replies to
    direction TEXT NOT NULL DEFAULT 'inbound', -- inbound (user→RDC) or outbound (RDC→user)
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT,                            -- plain text body
    body_html TEXT,                            -- HTML body (stored, not displayed)
    attachments JSON DEFAULT '[]',            -- [{filename, path, size_bytes, content_type}]
    processed_at TIMESTAMP,                    -- when orchestrator processed this
    task_id TEXT,                              -- task created from this specific message
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSON
);

CREATE INDEX IF NOT EXISTS idx_email_messages_thread ON email_messages(thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_messages_rfc_id ON email_messages(message_id);

-- migrate:down
DROP TABLE IF EXISTS email_messages;
DROP TABLE IF EXISTS email_threads;
