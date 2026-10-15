CREATE TABLE rulebook_reviews (
    rulebook_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 2),
    reviewer_id TEXT NOT NULL CHECK (length(btrim(reviewer_id)) > 0),
    action TEXT NOT NULL CHECK (action IN ('edit', 'confirm', 'reject', 'revise')),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (rulebook_id, version),
    FOREIGN KEY (rulebook_id, version)
        REFERENCES rulebook_versions (rulebook_id, version)
);
