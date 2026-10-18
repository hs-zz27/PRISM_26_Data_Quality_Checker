CREATE TABLE sample_reviews (
    audit_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    decision TEXT NOT NULL
        CHECK (decision IN ('pending_review', 'accepted', 'rejected', 'needs_correction')),
    reviewer_id TEXT NOT NULL CHECK (length(btrim(reviewer_id)) > 0),
    comment TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (audit_id, sample_id, sample_revision, revision),
    FOREIGN KEY (audit_id, sample_id, sample_revision)
        REFERENCES audit_samples (audit_id, sample_id, sample_revision)
);
