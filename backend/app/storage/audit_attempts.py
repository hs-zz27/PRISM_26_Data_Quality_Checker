"""Shared, read-only selection of the latest attempt for each audit sample.

Attempts retain their own evidence and reviews. Selection is by submission time,
never completion time or outcome: a failed recheck cannot resurrect an older
accepted result. Callers run this CTE in their existing database snapshot.
"""

AUDIT_FAMILY = """
    WITH RECURSIVE audit_family AS (
        SELECT audit_id, created_at, 0 AS depth, ARRAY[audit_id] AS visited
        FROM audit_runs WHERE audit_id = %s
        UNION ALL
        SELECT child.audit_id, child.created_at, parent.depth + 1,
               parent.visited || child.audit_id
        FROM audit_family parent
        JOIN audit_recheck_lineage link ON link.parent_audit_id = parent.audit_id
        JOIN audit_runs child ON child.audit_id = link.child_audit_id
        WHERE NOT child.audit_id = ANY(parent.visited)
    )
"""

LATEST_SAMPLES = AUDIT_FAMILY + """
    , selected_samples AS (
        SELECT DISTINCT ON (s.sample_id)
               s.audit_id, s.sample_id, s.sample_revision, original.ordinal
        FROM audit_family family
        JOIN audit_samples s ON s.audit_id = family.audit_id
        JOIN audit_samples original
          ON original.audit_id = (SELECT audit_id FROM audit_family WHERE depth = 0)
         AND original.sample_id = s.sample_id
        ORDER BY s.sample_id, family.created_at DESC, family.depth DESC,
                 family.audit_id COLLATE "C" DESC
    )
"""


def workspace_root(connection, audit_id: str) -> str:
    """Follow durable parent links; reject corrupt cycles rather than looping."""
    seen = set()
    while audit_id not in seen:
        seen.add(audit_id)
        row = connection.execute(
            "SELECT parent_audit_id FROM audit_recheck_lineage WHERE child_audit_id=%s",
            (audit_id,),
        ).fetchone()
        if row is None:
            return audit_id
        audit_id = row["parent_audit_id"]
    from .workflow_repository import WorkflowStorageError
    raise WorkflowStorageError("The audit attempt history is inconsistent.")
