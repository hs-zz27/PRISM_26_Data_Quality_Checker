CREATE TABLE rulebook_versions (
    rulebook_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    confirmation_state TEXT NOT NULL
        CHECK (confirmation_state IN ('draft', 'confirmed', 'rejected')),
    document JSONB NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    PRIMARY KEY (rulebook_id, version)
);
