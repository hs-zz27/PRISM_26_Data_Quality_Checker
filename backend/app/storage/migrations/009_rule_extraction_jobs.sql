-- Private inputs exist independently of the not-yet-published output book.
CREATE TABLE rule_extraction_requests (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
    rulebook_id TEXT NOT NULL UNIQUE CHECK (rulebook_id ~ '^rulebook_[0-9a-f]{32}$'),
    guideline_text TEXT NOT NULL CHECK (length(guideline_text) > 0),
    settings JSONB NOT NULL CHECK (jsonb_typeof(settings) = 'object'),
    policy JSONB NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
    started_at TIMESTAMPTZ,
    deadline TIMESTAMPTZ,
    CHECK ((started_at IS NULL AND deadline IS NULL)
        OR (started_at IS NOT NULL AND deadline IS NOT NULL AND deadline > started_at))
);

CREATE INDEX rule_extraction_deadline_idx ON rule_extraction_requests(deadline)
    WHERE started_at IS NOT NULL;
