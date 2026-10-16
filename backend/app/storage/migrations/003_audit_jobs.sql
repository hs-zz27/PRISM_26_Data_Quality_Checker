CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY CHECK (length(btrim(job_id)) > 0),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'rule_extraction', 'dataset_import', 'audit', 'recheck',
            'export', 'feedback_evaluation'
        )
    ),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled')
    ),
    progress JSONB NOT NULL CHECK (jsonb_typeof(progress) = 'object'),
    result JSONB CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
    error JSONB CHECK (error IS NULL OR jsonb_typeof(error) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    resource_kind TEXT CHECK (
        resource_kind IS NULL OR resource_kind IN ('rulebook', 'dataset', 'audit', 'export', 'feedback_evaluation')
    ),
    resource_id TEXT CHECK (resource_id IS NULL OR length(btrim(resource_id)) > 0),
    claimed_by TEXT CHECK (claimed_by IS NULL OR length(btrim(claimed_by)) > 0),
    claim_token TEXT,
    lease_until TIMESTAMPTZ,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    recovery_count INTEGER NOT NULL DEFAULT 0 CHECK (recovery_count >= 0),
    CHECK (status NOT IN ('running', 'cancelling') OR claim_token IS NOT NULL),
    CHECK (status NOT IN ('running', 'cancelling') OR lease_until IS NOT NULL),
    CHECK (status <> 'completed' OR (result IS NOT NULL AND error IS NULL)),
    CHECK (status <> 'failed' OR (error IS NOT NULL AND result IS NULL))
);

CREATE INDEX jobs_claimable_idx ON jobs (status, lease_until, created_at);

CREATE TABLE audit_runs (
    audit_id TEXT PRIMARY KEY CHECK (length(btrim(audit_id)) > 0),
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs (job_id),
    dataset_id TEXT NOT NULL CHECK (length(btrim(dataset_id)) > 0),
    dataset_root TEXT NOT NULL,
    rulebook_id TEXT NOT NULL CHECK (length(btrim(rulebook_id)) > 0),
    rulebook_version INTEGER NOT NULL CHECK (rulebook_version >= 1),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled')
    ),
    request JSONB NOT NULL CHECK (jsonb_typeof(request) = 'object'),
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    processed_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (processed_count <= sample_count),
    CHECK (failed_count <= processed_count)
);

CREATE INDEX audit_runs_status_idx ON audit_runs (status, created_at);

CREATE TABLE audit_samples (
    audit_id TEXT NOT NULL REFERENCES audit_runs (audit_id),
    sample_id TEXT NOT NULL CHECK (length(btrim(sample_id)) > 0),
    sample_revision INTEGER NOT NULL CHECK (sample_revision >= 1),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    claimed_by TEXT CHECK (claimed_by IS NULL OR length(btrim(claimed_by)) > 0),
    claim_token TEXT,
    lease_until TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    failure_id TEXT,
    PRIMARY KEY (audit_id, sample_id, sample_revision),
    UNIQUE (audit_id, ordinal),
    CHECK (status <> 'running' OR claim_token IS NOT NULL),
    CHECK (status <> 'running' OR lease_until IS NOT NULL)
);

CREATE INDEX audit_samples_claimable_idx ON audit_samples (audit_id, status, ordinal, lease_until);

CREATE TABLE audit_measurements (
    audit_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL,
    document JSONB NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (audit_id, sample_id, sample_revision),
    FOREIGN KEY (audit_id, sample_id, sample_revision)
        REFERENCES audit_samples (audit_id, sample_id, sample_revision)
);

CREATE TABLE audit_failures (
    failure_id TEXT PRIMARY KEY CHECK (length(btrim(failure_id)) > 0),
    audit_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK (length(btrim(stage)) > 0),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) > 0),
    detail TEXT NOT NULL CHECK (length(btrim(detail)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (audit_id, sample_id, sample_revision, stage, reason),
    FOREIGN KEY (audit_id, sample_id, sample_revision)
        REFERENCES audit_samples (audit_id, sample_id, sample_revision)
);

ALTER TABLE audit_samples
    ADD CONSTRAINT audit_samples_failure_fk
    FOREIGN KEY (failure_id) REFERENCES audit_failures (failure_id);

CREATE TABLE findings (
    finding_id TEXT PRIMARY KEY CHECK (length(btrim(finding_id)) > 0),
    audit_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL,
    object_id INTEGER,
    rulebook_id TEXT NOT NULL CHECK (length(btrim(rulebook_id)) > 0),
    rulebook_version INTEGER NOT NULL CHECK (rulebook_version >= 1),
    rule_id TEXT NOT NULL CHECK (length(btrim(rule_id)) > 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'fail', 'unsure', 'unavailable')),
    review_resolution TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_resolution IN ('pending', 'accepted', 'rejected', 'unsure')),
    document JSONB NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (audit_id, sample_id, sample_revision)
        REFERENCES audit_samples (audit_id, sample_id, sample_revision)
);

CREATE INDEX findings_audit_sample_idx ON findings (audit_id, sample_id, sample_revision, created_at);
