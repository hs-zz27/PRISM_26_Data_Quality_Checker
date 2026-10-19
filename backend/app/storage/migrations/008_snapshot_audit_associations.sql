ALTER TABLE audit_runs
    ADD COLUMN source_mode TEXT NOT NULL DEFAULT 'legacy'
        CHECK (source_mode IN ('legacy', 'snapshot'));

ALTER TABLE audit_samples
    ADD COLUMN manifest_dataset_id TEXT
        CHECK (manifest_dataset_id IS NULL OR length(btrim(manifest_dataset_id)) > 0);

ALTER TABLE audit_samples
    ADD CONSTRAINT audit_samples_manifest_fk
    FOREIGN KEY (manifest_dataset_id, sample_id, sample_revision)
    REFERENCES sample_asset_manifests (dataset_id, sample_id, sample_revision);

CREATE INDEX audit_samples_manifest_idx
    ON audit_samples (manifest_dataset_id, sample_id, sample_revision);
