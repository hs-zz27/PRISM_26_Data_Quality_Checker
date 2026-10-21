-- Additive reviewed-export ledger. No historical audit/review/job backfill.
CREATE TABLE reviewed_exports (
    export_id TEXT PRIMARY KEY CHECK (export_id ~ '^export_[0-9a-f]{32}$'),
    audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    status TEXT NOT NULL CHECK (status IN
        ('queued','running','cancelling','completed','failed','cancelled')),
    request JSONB NOT NULL CHECK (jsonb_typeof(request)='object'),
    created_at TIMESTAMPTZ NOT NULL,
    included_count BIGINT CHECK (included_count >= 0),
    excluded_count BIGINT CHECK (excluded_count >= 0),
    artifacts JSONB CHECK (artifacts IS NULL OR jsonb_typeof(artifacts)='object'),
    CHECK ((status='completed' AND included_count IS NOT NULL
              AND excluded_count IS NOT NULL AND artifacts IS NOT NULL)
        OR (status<>'completed' AND included_count IS NULL
              AND excluded_count IS NULL AND artifacts IS NULL))
);
CREATE INDEX reviewed_exports_collection_idx ON reviewed_exports(created_at DESC,export_id DESC);

CREATE TABLE reviewed_export_inputs (
    export_id TEXT PRIMARY KEY REFERENCES reviewed_exports(export_id),
    frozen JSONB NOT NULL CHECK (jsonb_typeof(frozen)='object'),
    started_at TIMESTAMPTZ,
    deadline TIMESTAMPTZ,
    CHECK ((started_at IS NULL AND deadline IS NULL)
        OR (started_at IS NOT NULL AND deadline IS NOT NULL AND deadline > started_at))
);

CREATE FUNCTION reviewed_export_input_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'export input cannot be deleted';
    END IF;
    IF NEW.export_id IS DISTINCT FROM OLD.export_id OR NEW.frozen IS DISTINCT FROM OLD.frozen
       OR OLD.started_at IS NOT NULL OR NEW.started_at IS NULL OR NEW.deadline IS NULL THEN
        RAISE EXCEPTION 'export input is immutable except initial attempt allocation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER reviewed_export_input_immutable BEFORE UPDATE OR DELETE ON reviewed_export_inputs
    FOR EACH ROW EXECUTE FUNCTION reviewed_export_input_guard();

-- Preserve resource provenance while allowing only coupled lifecycle/artifact updates.
CREATE FUNCTION reviewed_export_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE saved_job jobs%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'export identity is durable';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.export_id IS DISTINCT FROM OLD.export_id
        OR NEW.audit_id IS DISTINCT FROM OLD.audit_id
        OR NEW.job_id IS DISTINCT FROM OLD.job_id
        OR NEW.request IS DISTINCT FROM OLD.request
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR OLD.status IN ('completed','failed','cancelled')
    ) THEN
        RAISE EXCEPTION 'export provenance and terminal state are immutable';
    END IF;
    SELECT * INTO saved_job FROM jobs WHERE job_id = NEW.job_id;
    IF NOT FOUND OR saved_job.kind <> 'export'
       OR saved_job.resource_kind IS DISTINCT FROM 'export'
       OR saved_job.resource_id IS DISTINCT FROM NEW.export_id THEN
        RAISE EXCEPTION 'export job identity is inconsistent';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER reviewed_export_identity BEFORE INSERT OR UPDATE OR DELETE ON reviewed_exports
    FOR EACH ROW EXECUTE FUNCTION reviewed_export_identity_guard();
