"""Read-only audit views, composed within a single PostgreSQL snapshot."""

import json
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from ..models.audit import AuditFailure
from ..models.audit_read import (
    AssetReference,
    AuditSamplePage,
    FindingPage,
    SampleAssets,
    SampleAudit,
    SampleAuditSummary,
    SampleAttempt,
    ToolStatus,
)
from ..models.finding import FindingOutcome
from ..models.measurements import ASSET_LOAD_FAILURE_DETAIL
from ..models.review import SampleReview, SampleReviewDecision, SampleReviewRecord
from .workflow_repository import (
    AuditNotFoundError, AuditSampleNotFoundError, WorkflowStorageError,
    _FINDINGS_WITH_REVIEWS, _WorkflowRepository, _decode_reviewed_finding,
    _require_identifier, _validate_measurement_document,
)
from .audit_attempts import AUDIT_FAMILY, LATEST_SAMPLES, workspace_root


# Keep aggregation in SQL so filtering and pagination apply to the entire queue.
# Both methods use this same view; counts and captured-asset metadata always
# describe the same repeatable-read snapshot.
_ASSET_CONTENT_TYPE = "image/png"
# Largest OFFSET PostgreSQL accepts (a signed 64-bit integer).
_MAX_OFFSET = 9_223_372_036_854_775_807
_SAMPLE_VIEW_TEMPLATE = """
    SELECT s.audit_id, s.sample_id, s.sample_revision, {ordinal} AS ordinal,
           s.manifest_dataset_id,
           manifest.original_image_availability AS manifest_image_availability,
           manifest.raw_mask_availability AS manifest_mask_availability,
           s.status AS processing_status, s.incomplete,
           to_jsonb(latest) AS current_review,
           COALESCE(latest.decision, 'pending_review') AS review_status,
           counts.pass_count, counts.fail, counts.unsure, counts.unavailable,
           CASE
               WHEN s.status IN ('queued', 'running') THEN 'pending'
               WHEN counts.fail > 0 THEN 'flagged'
               WHEN counts.unsure > 0 THEN 'unsure'
               WHEN s.status <> 'completed' OR s.incomplete
                    OR counts.unavailable > 0 OR counts.pass_count = 0 THEN 'unavailable'
               ELSE 'no_issue'
           END AS tool_status
    FROM {source}
    LEFT JOIN sample_asset_manifests AS manifest
      ON manifest.dataset_id = s.manifest_dataset_id
     AND manifest.sample_id = s.sample_id
     AND manifest.sample_revision = s.sample_revision
    LEFT JOIN LATERAL (
        SELECT * FROM sample_reviews AS r
        WHERE (r.audit_id, r.sample_id, r.sample_revision) =
              (s.audit_id, s.sample_id, s.sample_revision)
        ORDER BY r.revision DESC LIMIT 1
    ) AS latest ON TRUE
    CROSS JOIN LATERAL (
        SELECT count(*) FILTER (WHERE outcome = 'pass') AS pass_count,
               count(*) FILTER (WHERE outcome = 'fail') AS fail,
               count(*) FILTER (WHERE outcome = 'unsure') AS unsure,
               count(*) FILTER (WHERE outcome = 'unavailable') AS unavailable
        FROM findings AS f
        WHERE (f.audit_id, f.sample_id, f.sample_revision) =
              (s.audit_id, s.sample_id, s.sample_revision)
    ) AS counts
    WHERE {selection}
"""

_SAMPLE_VIEW = _SAMPLE_VIEW_TEMPLATE.format(
    ordinal="s.ordinal", source="audit_samples AS s", selection="s.audit_id = %s",
)
_LATEST_SAMPLE_VIEW = _SAMPLE_VIEW_TEMPLATE.format(
    ordinal="chosen.ordinal",
    source="""selected_samples chosen JOIN audit_samples AS s
        ON (s.audit_id, s.sample_id, s.sample_revision) =
           (chosen.audit_id, chosen.sample_id, chosen.sample_revision)""",
    selection="TRUE",
)


def _asset_url(row: dict[str, Any], asset_kind: str) -> str:
    """Build a relative URL from the stored manifest association only."""
    return (
        "/api/v1/datasets/"
        f"{quote(row['manifest_dataset_id'], safe='')}/samples/"
        f"{quote(row['sample_id'], safe='')}/assets/{asset_kind}"
        f"?revision={row['sample_revision']}"
    )


def _asset_reference(
    row: dict[str, Any], *, asset_kind: str, availability_column: str,
) -> AssetReference | None:
    """Advertise captured availability without touching managed storage."""
    if (
        row["manifest_dataset_id"] is None
        or row[availability_column] != "available"
    ):
        return None
    return AssetReference(
        url=_asset_url(row, asset_kind),
        content_type=_ASSET_CONTENT_TYPE,
        revision=row["sample_revision"],
    )


def _assets(row: dict[str, Any]) -> SampleAssets:
    """Compose public links from the exact audit sample manifest pin."""
    return SampleAssets(
        image=_asset_reference(
            row, asset_kind="image", availability_column="manifest_image_availability",
        ),
        mask=_asset_reference(
            row, asset_kind="mask", availability_column="manifest_mask_availability",
        ),
        mask_preview=_asset_reference(
            row, asset_kind="mask_preview", availability_column="manifest_mask_availability",
        ),
        thumbnail=_asset_reference(
            row, asset_kind="thumbnail", availability_column="manifest_image_availability",
        ),
    )


def _summary(row: dict[str, Any]) -> SampleAuditSummary:
    try:
        thumbnail = _asset_reference(
            row, asset_kind="thumbnail", availability_column="manifest_image_availability",
        )
        return SampleAuditSummary(
            sample_id=row["sample_id"], sample_revision=row["sample_revision"],
            processing_status=row["processing_status"], incomplete=row["incomplete"],
            tool_status=row["tool_status"], review_status=row["review_status"],
            finding_counts={"pass": row["pass_count"], "fail": row["fail"],
                            "unsure": row["unsure"], "unavailable": row["unavailable"]},
            thumbnail_url=None if thumbnail is None else thumbnail.url,
            result_audit_id=row["audit_id"],
        )
    except ValidationError as error:
        raise WorkflowStorageError("A saved audit sample failed validation.") from error


def _public_measurements(document: dict[str, Any], sample_id: str) -> dict[str, Any]:
    """Keep stored evidence intact while hiding legacy loader exception details."""
    # Validation normalizes into a new document, so this projection cannot edit
    # either the database record or the caller's nested error dictionaries.
    public = _validate_measurement_document(document, sample_id)
    errors = public.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, dict) and error.get("stage") == "load":
                error["detail"] = ASSET_LOAD_FAILURE_DETAIL
    return public


class AuditReadRepository(_WorkflowRepository):
    """Compose queue and detail without starting work or changing review state."""

    @staticmethod
    def _get_audit(connection: Any, audit_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT dataset_id FROM audit_runs WHERE audit_id = %s", (audit_id,),
        ).fetchone()
        if row is None:
            raise AuditNotFoundError(audit_id)
        return row

    def list_samples(
        self, audit_id: str, *, limit: int = 20, offset: int = 0,
        tool_status: ToolStatus | None = None,
        review_status: SampleReviewDecision | None = None,
        include_rechecks: bool = False,
    ) -> AuditSamplePage:
        audit_id = _require_identifier(audit_id, "audit_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer.")
        filters = []
        parameters: list[Any] = [audit_id]
        for name, value, enum in (
            ("tool_status", tool_status, ToolStatus),
            ("review_status", review_status, SampleReviewDecision),
        ):
            if value is not None:
                filters.append(f"{name} = %s")
                parameters.append(enum(value).value)
        prefix = LATEST_SAMPLES if include_rechecks else ""
        view = _LATEST_SAMPLE_VIEW if include_rechecks else _SAMPLE_VIEW
        query = f"SELECT * FROM ({view}) AS samples"
        if filters:
            query += " WHERE " + " AND ".join(filters)
        with self._connection() as connection:
            self._get_audit(connection, audit_id)
            total = connection.execute(
                prefix + f"SELECT count(*) AS total FROM ({query}) AS filtered", parameters,
            ).fetchone()["total"]
            rows = connection.execute(
                prefix + query + " ORDER BY ordinal LIMIT %s OFFSET %s", [*parameters, limit, offset],
            ).fetchall()
            items = [_summary(row) for row in rows]
        return AuditSamplePage(
            items=items, limit=limit, offset=offset, total=total,
            next_offset=offset + len(items) if offset + len(items) < total else None,
        )

    def list_audit_findings(
        self, audit_id: str, *, limit: int = 20, offset: int = 0,
        sample_id: str | None = None, outcome: FindingOutcome | None = None,
        include_rechecks: bool = False,
    ) -> FindingPage:
        """Read one audit's findings in ascending finding_id order.

        Audit existence, the filtered total, the page and each finding's latest
        human review come from one repeatable-read snapshot. sample_id is only a
        filter: an unknown sample gives an empty page. Machine evidence is
        returned exactly as stored, never refreshed from current dataset heads.
        """
        audit_id = _require_identifier(audit_id, "audit_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100.")
        if type(offset) is not int or not 0 <= offset <= _MAX_OFFSET:
            raise ValueError(f"offset must be an integer between 0 and {_MAX_OFFSET}.")
        prefix = LATEST_SAMPLES if include_rechecks else ""
        filters = ["""EXISTS (SELECT 1 FROM selected_samples chosen
            WHERE (findings.audit_id, findings.sample_id, findings.sample_revision) =
                  (chosen.audit_id, chosen.sample_id, chosen.sample_revision))"""
            if include_rechecks else "findings.audit_id = %s"]
        parameters: list[Any] = [audit_id]
        if sample_id is not None:
            filters.append("findings.sample_id = %s")
            parameters.append(_require_identifier(sample_id, "sample_id"))
        if outcome is not None:
            filters.append("findings.outcome = %s")
            parameters.append(FindingOutcome(outcome).value)
        where = " WHERE " + " AND ".join(filters)
        with self._connection() as connection:
            self._get_audit(connection, audit_id)
            total = connection.execute(
                prefix + "SELECT count(*) AS total FROM findings" + where, parameters,
            ).fetchone()["total"]
            rows = connection.execute(
                prefix + _FINDINGS_WITH_REVIEWS + where + " ORDER BY findings.finding_id LIMIT %s OFFSET %s",
                [*parameters, limit, offset],
            ).fetchall()
            items = [_decode_reviewed_finding(row) for row in rows]
        return FindingPage(
            items=items, limit=limit, offset=offset, total=total,
            next_offset=offset + len(items) if offset + len(items) < total else None,
        )

    def get_sample(self, audit_id: str, sample_id: str, *, include_rechecks: bool = False) -> SampleAudit:
        audit_id = _require_identifier(audit_id, "audit_id")
        sample_id = _require_identifier(sample_id, "sample_id")
        with self._connection() as connection:
            audit = self._get_audit(connection, audit_id)
            prefix = LATEST_SAMPLES if include_rechecks else ""
            view = _LATEST_SAMPLE_VIEW if include_rechecks else _SAMPLE_VIEW
            rows = connection.execute(
                prefix + view + " AND s.sample_id = %s", (audit_id, sample_id),
            ).fetchall()
            if not rows:
                raise AuditSampleNotFoundError(audit_id, sample_id)
            # BatchAuditRequest allows one pin per sample. Never silently pick
            # evidence from an arbitrary revision if stored data violates that.
            if len(rows) != 1:
                raise WorkflowStorageError("The audit contains ambiguous sample revisions.")
            row = rows[0]
            result_audit_id = row["audit_id"]
            key = (result_audit_id, sample_id, row["sample_revision"])
            root_id = workspace_root(connection, audit_id)
            attempt_rows = connection.execute(
                AUDIT_FAMILY + """SELECT s.audit_id, a.job_id, s.sample_revision,
                    s.status AS processing_status, a.created_at, link.reviewer_id
                    FROM audit_family family
                    JOIN audit_runs a ON a.audit_id=family.audit_id
                    JOIN audit_samples s ON s.audit_id=a.audit_id
                    LEFT JOIN audit_recheck_lineage link ON link.child_audit_id=a.audit_id
                    WHERE s.sample_id=%s
                    ORDER BY family.created_at DESC, family.depth DESC,
                             family.audit_id COLLATE "C" DESC""", (root_id, sample_id),
            ).fetchall()
            measurement = connection.execute(
                """SELECT document FROM audit_measurements
                   WHERE (audit_id, sample_id, sample_revision) = (%s, %s, %s)""", key,
            ).fetchone()
            finding_rows = connection.execute(
                _FINDINGS_WITH_REVIEWS + """
                WHERE (findings.audit_id, findings.sample_id, findings.sample_revision) = (%s, %s, %s)
                ORDER BY findings.created_at, findings.finding_id""", key,
            ).fetchall()
            failure_rows = connection.execute(
                """SELECT * FROM audit_failures
                   WHERE (audit_id, sample_id, sample_revision) = (%s, %s, %s)
                   ORDER BY created_at, failure_id""", key,
            ).fetchall()
            try:
                review = (
                    SampleReviewRecord.model_validate_json(json.dumps(row["current_review"]))
                    if row["current_review"] is not None else
                    SampleReview(audit_id=result_audit_id, sample_id=sample_id, sample_revision=key[2])
                )
                return SampleAudit(
                    audit_id=result_audit_id, dataset_id=audit["dataset_id"], summary=_summary(row),
                    workspace_audit_id=root_id,
                    attempts=[SampleAttempt.model_validate(item) for item in attempt_rows],
                    assets=_assets(row),
                    measurements=None if measurement is None else
                    _public_measurements(measurement["document"], sample_id),
                    findings=[_decode_reviewed_finding(item) for item in finding_rows],
                    failures=[AuditFailure.model_validate(item) for item in failure_rows],
                    review=review,
                )
            except (TypeError, ValueError) as error:
                raise WorkflowStorageError("Saved audit evidence failed validation.") from error
