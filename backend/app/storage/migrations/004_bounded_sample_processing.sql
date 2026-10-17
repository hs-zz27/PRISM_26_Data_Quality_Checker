ALTER TABLE audit_runs
    ADD COLUMN incomplete_count INTEGER NOT NULL DEFAULT 0
        CHECK (incomplete_count >= 0 AND incomplete_count <= processed_count);

ALTER TABLE audit_samples
    ADD COLUMN processing_deadline TIMESTAMPTZ,
    ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
        CHECK (attempt_count >= 0 AND attempt_count <= 3),
    ADD COLUMN incomplete BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN last_attempt_operation_id TEXT,
    ADD COLUMN terminal_operation_id TEXT;

UPDATE audit_samples
SET processing_deadline = COALESCE(started_at, CURRENT_TIMESTAMP) + INTERVAL '900 seconds',
    attempt_count = GREATEST(attempt_count, 1)
WHERE status = 'running' AND processing_deadline IS NULL;

ALTER TABLE audit_samples
    ADD CONSTRAINT audit_samples_processing_deadline_ck
    CHECK (status <> 'running' OR processing_deadline IS NOT NULL);

CREATE INDEX audit_samples_processing_deadline_idx
    ON audit_samples (status, processing_deadline);
