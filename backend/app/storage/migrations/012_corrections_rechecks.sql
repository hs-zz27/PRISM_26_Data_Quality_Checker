-- Additive: no historical rows, reviewer attribution or files are fabricated.
CREATE TABLE correction_mask_uploads (
    mask_upload_id TEXT PRIMARY KEY CHECK (mask_upload_id ~ '^mask_[0-9a-f]{32}$'),
    dataset_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK (base_revision BETWEEN 1 AND 2147483646),
    document JSONB NOT NULL CHECK (jsonb_typeof(document) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > created_at),
    consumed_revision INTEGER,
    FOREIGN KEY (dataset_id,sample_id,base_revision)
        REFERENCES dataset_sample_revisions(dataset_id,sample_id,sample_revision),
    FOREIGN KEY (dataset_id,sample_id,consumed_revision)
        REFERENCES dataset_sample_revisions(dataset_id,sample_id,sample_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (consumed_revision IS NULL OR consumed_revision = base_revision + 1)
);
CREATE TABLE annotation_revision_provenance (
    dataset_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL CHECK (sample_revision >= 2),
    parent_revision INTEGER NOT NULL CHECK (parent_revision = sample_revision - 1),
    reviewer_id TEXT NOT NULL CHECK (length(reviewer_id) BETWEEN 1 AND 255 AND reviewer_id ~ '\S'),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 4096 AND reason ~ '\S'),
    source_audit_id TEXT REFERENCES audit_runs(audit_id),
    mask_changed BOOLEAN NOT NULL,
    mask_upload_id TEXT UNIQUE REFERENCES correction_mask_uploads(mask_upload_id),
    PRIMARY KEY (dataset_id,sample_id,sample_revision),
    FOREIGN KEY (dataset_id,sample_id,sample_revision)
        REFERENCES dataset_sample_revisions(dataset_id,sample_id,sample_revision),
    FOREIGN KEY (dataset_id,sample_id,parent_revision)
        REFERENCES dataset_sample_revisions(dataset_id,sample_id,sample_revision),
    CHECK (mask_changed = (mask_upload_id IS NOT NULL))
);
CREATE TRIGGER annotation_provenance_immutable BEFORE UPDATE OR DELETE ON annotation_revision_provenance
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();
CREATE TABLE audit_recheck_lineage (
    child_audit_id TEXT PRIMARY KEY REFERENCES audit_runs(audit_id),
    parent_audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),
    reviewer_id TEXT NOT NULL CHECK (length(reviewer_id) BETWEEN 1 AND 255 AND reviewer_id ~ '\S'),
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (child_audit_id <> parent_audit_id)
);
CREATE TRIGGER audit_recheck_lineage_immutable BEFORE UPDATE OR DELETE ON audit_recheck_lineage
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();
CREATE FUNCTION correction_mask_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Staged evidence identity is durable' USING ERRCODE = '23514';
    END IF;
    IF NEW.mask_upload_id IS DISTINCT FROM OLD.mask_upload_id
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.sample_id IS DISTINCT FROM OLD.sample_id
       OR NEW.base_revision IS DISTINCT FROM OLD.base_revision
       OR NEW.document IS DISTINCT FROM OLD.document
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR OLD.consumed_revision IS NOT NULL OR NEW.consumed_revision IS NULL THEN
        RAISE EXCEPTION 'Staged evidence is immutable and consumed once' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER correction_mask_once BEFORE UPDATE OR DELETE ON correction_mask_uploads
    FOR EACH ROW EXECUTE FUNCTION correction_mask_guard();
