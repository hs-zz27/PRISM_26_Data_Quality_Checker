-- Permanent human-readable names for audits and logical rulebooks.
--
-- Names live in their own tables. Internal IDs, relationships, rulebook
-- documents, audit requests, pins and hashes are not touched, so no
-- fingerprint, frozen input or historical snapshot changes.
--
-- Numbers come from sequences and are allocated by commit-time triggers, so
-- every insert path is covered and no MAX(number) + 1 race exists. Sequence
-- values are never reused; gaps (for example after a rolled-back insert) are
-- expected. Only titles can change after allocation.

CREATE SEQUENCE audit_display_number_seq AS INTEGER MINVALUE 1 NO CYCLE;
CREATE SEQUENCE rulebook_display_number_seq AS INTEGER MINVALUE 1 NO CYCLE;

CREATE TABLE audit_names (
    audit_id TEXT PRIMARY KEY REFERENCES audit_runs(audit_id),
    -- A root audit is its own root. Rechecks keep their root's number.
    root_audit_id TEXT NOT NULL REFERENCES audit_runs(audit_id),
    display_number INTEGER NOT NULL CHECK (display_number >= 1),
    recheck_number INTEGER CHECK (recheck_number IS NULL OR recheck_number >= 1),
    -- Per-root counter; only roots allocate recheck numbers from it.
    last_recheck_number INTEGER NOT NULL DEFAULT 0 CHECK (last_recheck_number >= 0),
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 255 AND title ~ '\S'),
    title_revision INTEGER NOT NULL DEFAULT 0 CHECK (title_revision >= 0),
    title_updated_at TIMESTAMPTZ,
    title_updated_by TEXT CHECK (title_updated_by IS NULL OR (length(title_updated_by) BETWEEN 1 AND 255 AND title_updated_by ~ '\S')),
    CHECK ((recheck_number IS NULL) = (root_audit_id = audit_id)),
    CHECK (recheck_number IS NULL OR last_recheck_number = 0),
    CHECK ((title_revision = 0) = (title_updated_at IS NULL)),
    CHECK ((title_updated_at IS NULL) = (title_updated_by IS NULL)),
    UNIQUE (display_number, recheck_number)
);
CREATE UNIQUE INDEX audit_names_root_number_idx ON audit_names (display_number) WHERE recheck_number IS NULL;
CREATE INDEX audit_names_root_idx ON audit_names (root_audit_id);

CREATE TABLE rulebook_names (
    rulebook_id TEXT PRIMARY KEY CHECK (length(btrim(rulebook_id)) > 0),
    display_number INTEGER NOT NULL UNIQUE CHECK (display_number >= 1),
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 255 AND title ~ '\S'),
    title_revision INTEGER NOT NULL DEFAULT 0 CHECK (title_revision >= 0),
    title_updated_at TIMESTAMPTZ,
    title_updated_by TEXT CHECK (title_updated_by IS NULL OR (length(title_updated_by) BETWEEN 1 AND 255 AND title_updated_by ~ '\S')),
    -- When the number was allocated, or for backfilled books the earliest
    -- recorded time; NULL when no history records a time. Never invented.
    first_recorded_at TIMESTAMPTZ,
    CHECK ((title_revision = 0) = (title_updated_at IS NULL)),
    CHECK ((title_updated_at IS NULL) = (title_updated_by IS NULL))
);

-- Attributed title edits, append-only.
CREATE TABLE display_title_edits (
    resource_kind TEXT NOT NULL CHECK (resource_kind IN ('audit', 'rulebook')),
    resource_id TEXT NOT NULL CHECK (length(btrim(resource_id)) > 0),
    title_revision INTEGER NOT NULL CHECK (title_revision >= 1),
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 255 AND title ~ '\S'),
    reviewer_id TEXT NOT NULL CHECK (length(reviewer_id) BETWEEN 1 AND 255 AND reviewer_id ~ '\S'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (resource_kind, resource_id, title_revision)
);
CREATE TRIGGER display_title_edits_immutable BEFORE UPDATE OR DELETE ON display_title_edits
    FOR EACH ROW EXECUTE FUNCTION dataset_catalogue_immutable();

-- Default titles are derived from stored data, never from a model.
-- Rulebook: the first nonblank guideline line without its list marker,
-- whitespace collapsed, shortened to 80 characters.
CREATE FUNCTION default_rulebook_title(guideline TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(NULLIF(
        CASE WHEN length(cleaned) > 80 THEN rtrim(left(cleaned, 79)) || '…' ELSE cleaned END, ''),
        'Untitled guidelines')
    FROM (
        SELECT btrim(regexp_replace(regexp_replace(
            (SELECT line FROM regexp_split_to_table(COALESCE(guideline, ''), E'\r\n|\n|\r') WITH ORDINALITY AS t(line, position)
             WHERE line ~ '\S' ORDER BY position LIMIT 1),
            '^\s*(\(?[0-9]+[.):]|[-*•])\s*', ''), '\s+', ' ', 'g')) AS cleaned
    ) AS first_line
$$;

-- Audit: the dataset name for a root audit; rechecks say what started them.
CREATE FUNCTION default_audit_title(target TEXT, is_recheck BOOLEAN) RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN is_recheck AND jsonb_typeof(run.request -> 'reviewer_feedback') = 'object'
             AND run.request -> 'reviewer_feedback' <> '{}'::jsonb THEN 'Feedback recheck'
        WHEN is_recheck THEN 'Recheck'
        ELSE COALESCE((SELECT dataset.name FROM datasets dataset WHERE dataset.dataset_id = run.dataset_id),
                      'Untitled audit')
    END
    FROM audit_runs run WHERE run.audit_id = target
$$;

CREATE FUNCTION name_audit(target TEXT) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    root TEXT;
    root_number INTEGER;
    suffix INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM audit_names WHERE audit_id = target) THEN
        RETURN;
    END IF;
    WITH RECURSIVE ancestors(audit_id, depth) AS (
        SELECT parent_audit_id, 1 FROM audit_recheck_lineage WHERE child_audit_id = target
        UNION ALL
        SELECT link.parent_audit_id, ancestors.depth + 1
        FROM audit_recheck_lineage link JOIN ancestors ON link.child_audit_id = ancestors.audit_id
        WHERE ancestors.depth < 100000
    )
    SELECT audit_id INTO root FROM ancestors ORDER BY depth DESC LIMIT 1;
    IF root IS NULL THEN
        INSERT INTO audit_names (audit_id, root_audit_id, display_number, title)
        VALUES (target, target, nextval('audit_display_number_seq'), default_audit_title(target, FALSE));
        RETURN;
    END IF;
    PERFORM name_audit(root);
    -- The row lock serializes concurrent rechecks of one root.
    UPDATE audit_names SET last_recheck_number = last_recheck_number + 1
    WHERE audit_id = root
    RETURNING display_number, last_recheck_number INTO root_number, suffix;
    INSERT INTO audit_names (audit_id, root_audit_id, display_number, recheck_number, title)
    VALUES (target, root, root_number, suffix, default_audit_title(target, TRUE));
END;
$$;

CREATE FUNCTION name_rulebook(target TEXT, guideline TEXT, recorded_at TIMESTAMPTZ) RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO rulebook_names (rulebook_id, display_number, title, first_recorded_at)
    SELECT target, nextval('rulebook_display_number_seq'), default_rulebook_title(guideline), recorded_at
    WHERE NOT EXISTS (SELECT 1 FROM rulebook_names WHERE rulebook_id = target)
    ON CONFLICT (rulebook_id) DO NOTHING
$$;

-- Backfill existing rulebooks: one number per logical book, ordered by the
-- earliest recorded time (extraction submission or first review), then ID.
-- Books with no recorded time follow, ordered by ID; no time is invented.
WITH books AS (
    SELECT ids.rulebook_id,
        (SELECT first.document ->> 'guideline_text' FROM rulebook_versions first
         WHERE first.rulebook_id = ids.rulebook_id ORDER BY first.version LIMIT 1) AS guideline,
        LEAST(
            (SELECT job.created_at FROM rule_extraction_requests request
             JOIN jobs job ON job.job_id = request.job_id WHERE request.rulebook_id = ids.rulebook_id),
            (SELECT min(review.created_at) FROM rulebook_reviews review WHERE review.rulebook_id = ids.rulebook_id)
        ) AS recorded_at
    FROM (SELECT DISTINCT rulebook_id FROM rulebook_versions) AS ids
)
INSERT INTO rulebook_names (rulebook_id, display_number, title, first_recorded_at)
SELECT rulebook_id,
    row_number() OVER (ORDER BY recorded_at ASC NULLS LAST, rulebook_id COLLATE "C"),
    default_rulebook_title(guideline), recorded_at
FROM books;

-- Backfill root audits by creation time, then ID.
INSERT INTO audit_names (audit_id, root_audit_id, display_number, title)
SELECT run.audit_id, run.audit_id,
    row_number() OVER (ORDER BY run.created_at, run.audit_id COLLATE "C"),
    default_audit_title(run.audit_id, FALSE)
FROM audit_runs run
WHERE NOT EXISTS (SELECT 1 FROM audit_recheck_lineage link WHERE link.child_audit_id = run.audit_id);

-- Backfill rechecks: each takes its original root's number and a suffix in
-- creation order within that root, including rechecks of rechecks.
WITH RECURSIVE chain(child, ancestor, depth) AS (
    SELECT child_audit_id, parent_audit_id, 1 FROM audit_recheck_lineage
    UNION ALL
    SELECT chain.child, link.parent_audit_id, chain.depth + 1
    FROM chain JOIN audit_recheck_lineage link ON link.child_audit_id = chain.ancestor
    WHERE chain.depth < 100000
), roots AS (
    SELECT DISTINCT ON (child) child, ancestor AS root FROM chain ORDER BY child, depth DESC
)
INSERT INTO audit_names (audit_id, root_audit_id, display_number, recheck_number, title)
SELECT roots.child, roots.root, root_name.display_number,
    row_number() OVER (PARTITION BY roots.root ORDER BY run.created_at, run.audit_id COLLATE "C"),
    default_audit_title(roots.child, TRUE)
FROM roots
JOIN audit_runs run ON run.audit_id = roots.child
JOIN audit_names root_name ON root_name.audit_id = roots.root;

UPDATE audit_names AS root SET last_recheck_number = counts.highest
FROM (SELECT root_audit_id, max(recheck_number) AS highest FROM audit_names
      WHERE recheck_number IS NOT NULL GROUP BY root_audit_id) AS counts
WHERE root.audit_id = counts.root_audit_id;

-- New numbers continue after the backfilled ones (no-op when there are none).
SELECT setval('audit_display_number_seq', (SELECT max(display_number) FROM audit_names));
SELECT setval('rulebook_display_number_seq', (SELECT max(display_number) FROM rulebook_names));

-- Allocate at commit, when a recheck's lineage row already exists.
CREATE FUNCTION name_new_audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM name_audit(NEW.audit_id);
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER audit_runs_display_name AFTER INSERT ON audit_runs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION name_new_audit();

CREATE FUNCTION name_new_rulebook() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM name_rulebook(NEW.rulebook_id, NEW.document ->> 'guideline_text', now());
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER rulebook_versions_display_name AFTER INSERT ON rulebook_versions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION name_new_rulebook();

-- Numbers and relationships are permanent; only titles (and a root's recheck
-- counter, upwards) may change. Names are never deleted, so never reused.
CREATE FUNCTION audit_name_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Display names are permanent' USING ERRCODE = '23514';
    END IF;
    IF NEW.audit_id IS DISTINCT FROM OLD.audit_id
        OR NEW.root_audit_id IS DISTINCT FROM OLD.root_audit_id
        OR NEW.display_number IS DISTINCT FROM OLD.display_number
        OR NEW.recheck_number IS DISTINCT FROM OLD.recheck_number
        OR NEW.last_recheck_number < OLD.last_recheck_number
        OR NEW.title_revision < OLD.title_revision THEN
        RAISE EXCEPTION 'Audit display numbers are permanent' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION rulebook_name_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Display names are permanent' USING ERRCODE = '23514';
    END IF;
    IF NEW.rulebook_id IS DISTINCT FROM OLD.rulebook_id
        OR NEW.display_number IS DISTINCT FROM OLD.display_number
        OR NEW.first_recorded_at IS DISTINCT FROM OLD.first_recorded_at
        OR NEW.title_revision < OLD.title_revision THEN
        RAISE EXCEPTION 'Rulebook display numbers are permanent' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER audit_names_permanent BEFORE UPDATE OR DELETE ON audit_names
    FOR EACH ROW EXECUTE FUNCTION audit_name_guard();
CREATE TRIGGER rulebook_names_permanent BEFORE UPDATE OR DELETE ON rulebook_names
    FOR EACH ROW EXECUTE FUNCTION rulebook_name_guard();
