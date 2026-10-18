CREATE TABLE finding_reviews (
    finding_id TEXT NOT NULL REFERENCES findings (finding_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    resolution TEXT NOT NULL CHECK (resolution IN ('accepted', 'rejected', 'unsure')),
    reviewer_id TEXT NOT NULL CHECK (length(btrim(reviewer_id)) > 0),
    comment TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (finding_id, revision)
);
