-- Additive source proposal. Coordinate number 013 against the applying checkout.
CREATE TABLE feedback_sets (
    resource_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('policy','truth')),
    document JSONB NOT NULL CHECK (jsonb_typeof(document)='object'),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TRIGGER feedback_sets_immutable BEFORE UPDATE OR DELETE ON feedback_sets
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();
CREATE TABLE feedback_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
    policy_id TEXT NOT NULL REFERENCES feedback_sets(resource_id),
    truth_id TEXT NOT NULL REFERENCES feedback_sets(resource_id),
    input JSONB NOT NULL CHECK (jsonb_typeof(input)='object'),
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    metrics JSONB,
    deadline TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE feedback_case_results (
    evaluation_id TEXT NOT NULL REFERENCES feedback_evaluations(evaluation_id),
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 0 AND 99),
    document JSONB NOT NULL CHECK (jsonb_typeof(document)='object'),
    PRIMARY KEY(evaluation_id,ordinal)
);
CREATE TRIGGER feedback_case_results_immutable BEFORE UPDATE OR DELETE ON feedback_case_results
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();
CREATE TABLE feedback_activations (
    policy_id TEXT PRIMARY KEY REFERENCES feedback_sets(resource_id),
    evaluation_id TEXT NOT NULL REFERENCES feedback_evaluations(evaluation_id),
    reviewer_id TEXT NOT NULL CHECK (reviewer_id ~ '\S'),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TRIGGER feedback_activations_immutable BEFORE UPDATE OR DELETE ON feedback_activations
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();
CREATE FUNCTION feedback_evaluation_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Feedback input is durable' USING ERRCODE='23514';
    END IF;
    IF (NEW.evaluation_id,NEW.job_id,NEW.policy_id,NEW.truth_id,NEW.input,NEW.input_sha256,NEW.created_at)
       IS DISTINCT FROM
       (OLD.evaluation_id,OLD.job_id,OLD.policy_id,OLD.truth_id,OLD.input,OLD.input_sha256,OLD.created_at)
       OR (OLD.metrics IS NOT NULL AND NEW.metrics IS DISTINCT FROM OLD.metrics)
       OR (OLD.deadline IS NOT NULL AND NEW.deadline IS DISTINCT FROM OLD.deadline) THEN
        RAISE EXCEPTION 'Feedback pins/results are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER feedback_evaluation_immutable BEFORE UPDATE OR DELETE ON feedback_evaluations
    FOR EACH ROW EXECUTE FUNCTION feedback_evaluation_guard();
CREATE INDEX feedback_sets_newest ON feedback_sets(kind,created_at DESC,resource_id COLLATE "C");
CREATE INDEX feedback_evaluations_newest ON feedback_evaluations(created_at DESC,evaluation_id COLLATE "C");
CREATE FUNCTION feedback_validate_links() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='feedback_evaluations' THEN
        IF NOT EXISTS (SELECT 1 FROM jobs j WHERE j.job_id=NEW.job_id
            AND j.kind='feedback_evaluation' AND j.resource_kind='feedback_evaluation'
            AND j.resource_id=NEW.evaluation_id)
           OR NOT EXISTS (SELECT 1 FROM feedback_sets s WHERE s.resource_id=NEW.policy_id AND s.kind='policy')
           OR NOT EXISTS (SELECT 1 FROM feedback_sets s WHERE s.resource_id=NEW.truth_id AND s.kind='truth') THEN
            RAISE EXCEPTION 'Feedback resource association invalid' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM feedback_evaluations e JOIN jobs j USING(job_id)
            WHERE e.evaluation_id=NEW.evaluation_id AND e.policy_id=NEW.policy_id
            AND j.status='completed' AND e.metrics IS NOT NULL) THEN
            RAISE EXCEPTION 'Feedback activation association invalid' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER feedback_evaluation_links BEFORE INSERT ON feedback_evaluations
    FOR EACH ROW EXECUTE FUNCTION feedback_validate_links();
CREATE TRIGGER feedback_activation_links BEFORE INSERT ON feedback_activations
    FOR EACH ROW EXECUTE FUNCTION feedback_validate_links();
