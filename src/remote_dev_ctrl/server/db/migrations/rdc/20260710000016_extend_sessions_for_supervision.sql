-- migrate:up

ALTER TABLE sessions ADD COLUMN goal TEXT;
ALTER TABLE sessions ADD COLUMN acceptance_criteria JSON NOT NULL DEFAULT '[]';
ALTER TABLE sessions ADD COLUMN control_owner TEXT NOT NULL DEFAULT 'none';
ALTER TABLE sessions ADD COLUMN current_step TEXT;
ALTER TABLE sessions ADD COLUMN blocker TEXT;
ALTER TABLE sessions ADD COLUMN policy JSON NOT NULL DEFAULT '{}';
ALTER TABLE sessions ADD COLUMN budget JSON;
ALTER TABLE sessions ADD COLUMN unresolved_risks JSON NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS session_approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    summary TEXT NOT NULL,
    details JSON NOT NULL DEFAULT '{}',
    requested_by TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT,
    decision_reason TEXT,
    requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS session_evidence (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    title TEXT NOT NULL,
    data JSON NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS session_criteria (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    criterion_index INTEGER NOT NULL,
    criterion TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unevaluated',
    note TEXT,
    evidence_ids JSON NOT NULL DEFAULT '[]',
    evaluated_by TEXT,
    evaluated_at TIMESTAMP,
    PRIMARY KEY (session_id, criterion_index)
);

CREATE TABLE IF NOT EXISTS session_feedback (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    useful_to_repeat BOOLEAN NOT NULL,
    left_workstation BOOLEAN NOT NULL DEFAULT 0,
    remote_device TEXT,
    approval_count INTEGER NOT NULL DEFAULT 0,
    intervention_count INTEGER NOT NULL DEFAULT 0,
    return_control_succeeded BOOLEAN,
    evidence_sufficient BOOLEAN,
    minutes_saved INTEGER,
    trust_failures TEXT,
    biggest_improvement TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_session_approvals_session ON session_approvals(session_id, status);
CREATE INDEX IF NOT EXISTS idx_session_evidence_session ON session_evidence(session_id, observed_at);

-- migrate:down

DROP TABLE IF EXISTS session_feedback;
DROP TABLE IF EXISTS session_criteria;
DROP TABLE IF EXISTS session_evidence;
DROP TABLE IF EXISTS session_approvals;
