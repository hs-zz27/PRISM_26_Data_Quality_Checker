-- Additive only: legacy manifests/audits have no new mandatory parent and no backfill.
CREATE TABLE datasets (
    dataset_id TEXT PRIMARY KEY CHECK (length(dataset_id) BETWEEN 1 AND 255 AND dataset_id ~ '\S'),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255 AND name ~ '\S'),
    format TEXT NOT NULL CHECK (format = 'penn_fudan'),
    status TEXT NOT NULL CHECK (status IN ('importing', 'ready', 'invalid', 'failed', 'cancelled')),
    sample_count BIGINT CHECK (sample_count >= 0),
    object_count BIGINT CHECK (object_count >= 0),
    import_job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    created_at TIMESTAMPTZ NOT NULL,
    issues JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(issues) = 'array'),
    UNIQUE (dataset_id, import_job_id),
    CHECK (jsonb_array_length(issues) <= 256),
    CHECK (status NOT IN ('invalid', 'failed') OR jsonb_array_length(issues) > 0),
    CHECK (object_count IS NULL OR sample_count IS NOT NULL),
    CHECK (sample_count IS DISTINCT FROM 0 OR object_count IS NULL OR object_count = 0),
    CHECK (status <> 'ready' OR (sample_count IS NOT NULL AND sample_count > 0))
);
CREATE INDEX datasets_order_idx ON datasets (created_at, dataset_id COLLATE "C");

CREATE TABLE dataset_import_requests (
    job_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL UNIQUE,
    archive_key TEXT NOT NULL CHECK (
        length(archive_key) BETWEEN 1 AND 1024 AND archive_key = btrim(archive_key)
        AND archive_key !~ '^/' AND archive_key !~ '^[A-Za-z]:'
        AND position(chr(92) IN archive_key) = 0
        AND archive_key !~ '(^|/)[.][.]?(/|$)' AND archive_key !~ '//'
        AND right(archive_key, 1) <> '/'
    ),
    archive_sha256 TEXT NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    archive_size_bytes BIGINT NOT NULL CHECK (archive_size_bytes > 0),
    policy JSONB NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
    started_at TIMESTAMPTZ,
    deadline TIMESTAMPTZ,
    UNIQUE (job_id, dataset_id),
    FOREIGN KEY (dataset_id, job_id) REFERENCES datasets(dataset_id, import_job_id),
    CHECK ((started_at IS NULL AND deadline IS NULL)
        OR (started_at IS NOT NULL AND deadline IS NOT NULL AND deadline > started_at))
);
-- Acceptance must commit all three rows, but may insert dataset before its ledger.
ALTER TABLE datasets ADD CONSTRAINT datasets_request_fk
    FOREIGN KEY (import_job_id, dataset_id)
    REFERENCES dataset_import_requests(job_id, dataset_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE dataset_samples (
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
    sample_id TEXT NOT NULL CHECK (length(sample_id) BETWEEN 1 AND 255 AND sample_id ~ '\S'),
    ordinal BIGINT NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (dataset_id, sample_id),
    UNIQUE (dataset_id, ordinal)
);

CREATE TABLE dataset_sample_revisions (
    dataset_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL CHECK (sample_revision >= 1),
    -- JSON text (not JSONB) preserves escaped NUL in source labels. Never parse
    -- arbitrary annotation text into SQL expressions; repository reads validate it.
    sample_document TEXT NOT NULL CHECK (length(sample_document) > 0),
    manifest_document JSONB NOT NULL CHECK (jsonb_typeof(manifest_document) = 'object'),
    annotation_object_count BIGINT CHECK (annotation_object_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dataset_id, sample_id, sample_revision),
    FOREIGN KEY (dataset_id, sample_id) REFERENCES dataset_samples(dataset_id, sample_id),
    FOREIGN KEY (dataset_id, sample_id, sample_revision)
        REFERENCES sample_asset_manifests(dataset_id, sample_id, sample_revision),
    CHECK (manifest_document ->> 'dataset_id' IS NOT NULL
        AND manifest_document ->> 'dataset_id' = dataset_id),
    CHECK (manifest_document ->> 'sample_id' IS NOT NULL
        AND manifest_document ->> 'sample_id' = sample_id),
    CHECK (manifest_document ->> 'sample_revision' IS NOT NULL
        AND (manifest_document ->> 'sample_revision')::BIGINT = sample_revision)
);

CREATE TABLE dataset_sample_heads (
    dataset_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_revision INTEGER NOT NULL,
    PRIMARY KEY (dataset_id, sample_id),
    FOREIGN KEY (dataset_id, sample_id, sample_revision)
        REFERENCES dataset_sample_revisions(dataset_id, sample_id, sample_revision)
);
-- At commit, no published identity can exist without an exact revision-backed head.
ALTER TABLE dataset_samples ADD CONSTRAINT dataset_samples_head_fk
    FOREIGN KEY (dataset_id, sample_id) REFERENCES dataset_sample_heads(dataset_id, sample_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION dataset_catalogue_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Catalogue identity and revision rows are immutable'
        USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER dataset_samples_immutable BEFORE UPDATE OR DELETE ON dataset_samples
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();
CREATE TRIGGER dataset_revisions_immutable BEFORE UPDATE OR DELETE ON dataset_sample_revisions
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();

CREATE FUNCTION dataset_request_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE saved_job jobs%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Import request identity is durable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.job_id IS DISTINCT FROM OLD.job_id
        OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
        OR NEW.archive_key IS DISTINCT FROM OLD.archive_key
        OR NEW.archive_sha256 IS DISTINCT FROM OLD.archive_sha256
        OR NEW.archive_size_bytes IS DISTINCT FROM OLD.archive_size_bytes
        OR NEW.policy IS DISTINCT FROM OLD.policy
        OR (OLD.started_at IS NOT NULL AND (
            NEW.started_at IS DISTINCT FROM OLD.started_at OR NEW.deadline IS DISTINCT FROM OLD.deadline))
    ) THEN
        RAISE EXCEPTION 'Import request identity and original deadline are immutable'
            USING ERRCODE = '23514';
    END IF;
    -- Phase 2 writers must lock the job first. This validation never adopts a generic job.
    SELECT * INTO saved_job FROM jobs WHERE job_id = NEW.job_id;
    IF NOT FOUND OR saved_job.kind <> 'dataset_import'
        OR saved_job.resource_kind IS DISTINCT FROM 'dataset'
        OR saved_job.resource_id IS DISTINCT FROM NEW.dataset_id THEN
        RAISE EXCEPTION 'Import job and dataset identities disagree' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER dataset_request_identity BEFORE INSERT OR UPDATE OR DELETE ON dataset_import_requests
    FOR EACH ROW EXECUTE FUNCTION dataset_request_guard();

-- Preserve immutable dataset identity while allowing Phase 2 lifecycle/count updates.
CREATE FUNCTION dataset_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Dataset identity is durable' USING ERRCODE = '23514';
    END IF;
    IF NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
        OR NEW.import_job_id IS DISTINCT FROM OLD.import_job_id
        OR NEW.name IS DISTINCT FROM OLD.name OR NEW.format IS DISTINCT FROM OLD.format
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Dataset source identity is immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER dataset_identity_immutable BEFORE UPDATE OR DELETE ON datasets
    FOR EACH ROW EXECUTE FUNCTION dataset_identity_guard();

CREATE FUNCTION dataset_head_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Catalogue head cannot be removed' USING ERRCODE = '23514';
    END IF;
    IF NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
        OR NEW.sample_id IS DISTINCT FROM OLD.sample_id
        OR NEW.sample_revision <= OLD.sample_revision THEN
        RAISE EXCEPTION 'A head update must advance the same sample identity'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER dataset_head_monotonic BEFORE UPDATE OR DELETE ON dataset_sample_heads
    FOR EACH ROW EXECUTE FUNCTION dataset_head_guard();
