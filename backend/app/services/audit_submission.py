"""Connect imported catalogue revisions to the existing batch audit engine."""

from ..checks.registry import RuleParameterError
from ..models.audit import AuditSourceMode, BatchAuditRequest
from ..models.audit_submission import AuditRequest, AuditStarted
from ..storage.dataset_repository import DatasetRepository
from .batch_audit import BatchAuditService


class AuditRuleParametersError(ValueError):
    """The selected book contains invalid parameters for a supported check."""


class AuditSubmissionService:
    def __init__(self, *, catalogue: DatasetRepository, batch: BatchAuditService) -> None:
        self.catalogue = catalogue
        self.batch = batch

    def start(self, request: AuditRequest) -> AuditStarted:
        """Pin a ready catalogue and exact confirmed book before durable acceptance."""
        if not isinstance(request, AuditRequest):
            raise TypeError("request must be an AuditRequest instance.")
        request = AuditRequest.model_validate(request.model_dump())
        pins = self.catalogue.resolve_audit_samples(request.dataset_id, request.sample_ids)
        batch_request = BatchAuditRequest(
            dataset_id=request.dataset_id,
            dataset_root="",  # Snapshot execution uses only the persisted manifests.
            rulebook_id=request.rulebook_id,
            rulebook_version=request.rulebook_version,
            sample_ids=list(pins),
            sample_revisions={sample_id: pin.revision for sample_id, pin in pins.items()},
            source_mode=AuditSourceMode.SNAPSHOT,
        )
        try:
            audit, job = self.batch.start(
                batch_request,
                sample_manifests={sample_id: pin.manifest for sample_id, pin in pins.items()},
            )
        except RuleParameterError as error:
            raise AuditRuleParametersError("The selected rule parameters are invalid.") from error
        return AuditStarted(audit=audit, job=job)
