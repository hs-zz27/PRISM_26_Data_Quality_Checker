"""Finite feedback snapshots. Human truth is separate from machine proposals."""
from __future__ import annotations
from hashlib import sha256
import json
from typing import Annotated, Generic, Literal, TypeVar
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from .asset_manifest import SampleAssetManifest
from .identifiers import Identifier, Revision
from .finding import FindingReview
from .job import Job
from .rule import Rule
from .enclosure import EnclosureEvidence, EnclosureExecutionPin, enclosure_case_identity
from .visual_assessment import VisualAssessment
Text = Annotated[str, StringConstraints(min_length=1, max_length=4096, pattern=r'\S')]
Digest = Annotated[str, StringConstraints(pattern='^[0-9a-f]{64}$')]
Binary = Literal['pass', 'fail']
Outcome = Literal['pass', 'fail', 'unsure', 'unavailable']
ExampleIdentifier = Annotated[str, StringConstraints(min_length=1, max_length=2048, pattern='^example_[A-Za-z0-9_-]+\\.[1-9][0-9]{0,9}$')]

class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, revalidate_instances='always')

    @field_validator(
        'version', 'policy_version', 'ground_truth_version',
        'expected_policy_version', 'schema_version', 'temperature',
        mode='before', check_fields=False,
    )
    @classmethod
    def original_integer(cls, value):
        """Literal integers must not normalize booleans or floating-point inputs."""
        if type(value) is not int:
            raise ValueError('Versions/options must be original integers, not booleans or floats.')
        return value

    @model_validator(mode='after')
    def valid_text(self):
        """Check text after parsing so strict JSON datetime decoding is preserved."""

        def visit(item):
            if isinstance(item, str):
                item.encode('utf-8', errors='strict')
                if '\x00' in item:
                    raise ValueError('NUL is not supported.')
            elif isinstance(item, BaseModel):
                for key, child in item:
                    visit(key)
                    visit(child)
            elif isinstance(item, dict):
                for key, child in item.items():
                    visit(key)
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)
        visit(self)
        return self

def checked(model, value):
    """Validate original Python values first, before JSON can normalize them."""
    values = value.model_dump(mode='python') if isinstance(value, model) else value
    return model.model_validate(values)

def fingerprint(value) -> str:
    document = value.model_dump(mode='json') if isinstance(value, BaseModel) else value
    return sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def unit_id(manifest, target):
    return fingerprint({'manifest': manifest.model_dump(mode='json'), 'target': target.model_dump(mode='json')})

class RulePin(Record):
    rulebook_id: Identifier
    rulebook_version: Revision
    rule: Rule
    semantic_sha256: Digest

    @model_validator(mode='before')
    @classmethod
    def pin_semantics(cls, value):
        if isinstance(value, dict) and 'semantic_sha256' not in value and ('rule' in value):
            value = dict(value, semantic_sha256=fingerprint(value['rule']))
        return value

    @model_validator(mode='after')
    def applicable(self):
        if self.semantic_sha256 != fingerprint(self.rule):
            raise ValueError('Rule semantic hash differs.')
        if self.rule.check_name not in {'annotation_guideline', 'pedestrian_box_enclosure'} or self.rule.parameters or self.rule.confirmation_state.value != 'confirmed':
            raise ValueError('A confirmed, parameter-free visual check is required.')
        return self

class CaseReference(Record):
    audit_id: Identifier
    sample_id: Identifier
    sample_revision: Revision
    rule_id: Identifier

class Adjudication(CaseReference):
    expected: Binary
    reason: Text

class CaseSnapshot(Record):
    reference: CaseReference
    target: RulePin
    manifest: SampleAssetManifest
    expected: Binary
    reviewer_id: Identifier
    reviewed_at: AwareDatetime
    reason: Text
    finding_id: Identifier | None = None
    finding_review: FindingReview | None = None
    model_execution: ExecutionPin | None = None
    model_proposal: RecordedPrediction | None = None
    correction_parent_revision: Revision | None = None
    correction_reviewer_id: Identifier | None = None

    @model_validator(mode='after')
    def identity(self):
        if self.reference.sample_id != self.manifest.sample_id or self.reference.sample_revision != self.manifest.sample_revision or self.reference.rule_id != self.target.rule.rule_id:
            raise ValueError('Case identity differs from its immutable evidence.')
        if any((e.availability.value != 'available' or not e.sha256 or (not e.size_bytes) for e in (self.manifest.original_image, self.manifest.annotation_text))):
            raise ValueError('Image and annotation evidence are required.')
        if (self.finding_id is None) != (self.finding_review is None):
            raise ValueError('Finding provenance must be complete.')
        if self.finding_id is not None and (self.model_execution is None or self.model_proposal is None):
            raise ValueError('Approved examples require frozen model provenance.')
        if self.finding_id is None and (self.model_execution is not None or self.model_proposal is not None):
            raise ValueError('Adjudications do not invent a machine proposal.')
        enclosure = self.target.rule.check_name == 'pedestrian_box_enclosure'
        if self.model_proposal is not None and (not self.model_proposal.model_verified or self.model_proposal.prediction.case_id != unit_id(self.manifest, self.target) or self.model_proposal.prediction.rule_id != self.reference.rule_id):
            raise ValueError('Approved proposal identity differs.')
        if self.finding_review is not None and (self.finding_review.resolution.value not in ({'accepted', 'rejected'} if enclosure else {'accepted'}) or self.finding_review.revision < 1 or self.finding_review.reviewer_id != self.reviewer_id or (self.finding_review.updated_at != self.reviewed_at)):
            raise ValueError('Examples need current accepted, attributed review evidence.')
        if self.model_proposal is not None:
            proposal = self.model_proposal
            expected = proposal.prediction.outcome
            if enclosure:
                evidence = proposal.enclosure
                if evidence is None or self.model_execution.enclosure != evidence.execution:
                    raise ValueError('Enclosure examples require frozen per-box execution evidence.')
                if evidence.boxes_intended != 1 or len(evidence.boxes) != 1 or not evidence.boxes[0].model_verified:
                    raise ValueError('Only single-box reviewed samples establish a per-box example.')
                if expected not in {'pass', 'fail'}:
                    raise ValueError('Enclosure examples require a binary model proposal.')
                identity = enclosure_case_identity(rulebook_id=self.target.rulebook_id, rulebook_version=self.target.rulebook_version,
                    rule_id=self.reference.rule_id, sample_id=self.manifest.sample_id,
                    sample_revision=self.manifest.sample_revision, image_sha256=self.manifest.original_image.sha256,
                    annotation_sha256=self.manifest.annotation_text.sha256)
                if (evidence.case_id != identity or evidence.image_sha256 != self.manifest.original_image.sha256
                        or evidence.annotation_sha256 != self.manifest.annotation_text.sha256):
                    raise ValueError('Enclosure source identity differs.')
                if self.finding_review.resolution.value == 'rejected':
                    if not (self.finding_review.comment or '').strip():
                        raise ValueError('An enclosure override requires the human reason.')
                    expected = 'fail' if expected == 'pass' else 'pass'
            elif proposal.enclosure is not None or self.model_execution.enclosure is not None:
                raise ValueError('Generic guideline provenance cannot contain enclosure execution.')
            if expected != self.expected:
                raise ValueError('Example answer differs from its human review.')
        if (self.correction_parent_revision is None) != (self.correction_reviewer_id is None):
            raise ValueError('Correction lineage must be complete when present.')
        return self

class CaseView(Record):
    case_id: Digest
    example_id: ExampleIdentifier | None
    reference: CaseReference
    dataset_id: Identifier
    target: RulePin
    expected: Binary
    image_sha256: Digest
    annotation_sha256: Digest
    reviewer_id: Identifier
    reviewed_at: AwareDatetime
    reason: Text
    finding_id: Identifier | None
    review_revision: int = Field(ge=0, le=2147483647)
    correction_parent_revision: Revision | None
    correction_reviewer_id: Identifier | None
    image_url: Text

def case_view(case: CaseSnapshot) -> CaseView:
    from urllib.parse import quote
    from base64 import urlsafe_b64encode
    example_id = None if case.finding_id is None else 'example_' + urlsafe_b64encode(case.finding_id.encode()).decode().rstrip('=') + '.' + str(case.finding_review.revision)
    source = case.manifest
    return CaseView(case_id=unit_id(case.manifest, case.target), example_id=example_id, reference=case.reference, dataset_id=source.dataset_id, target=case.target, expected=case.expected, image_sha256=source.original_image.sha256, annotation_sha256=source.annotation_text.sha256, reviewer_id=case.reviewer_id, reviewed_at=case.reviewed_at, reason=case.reason, finding_id=case.finding_id, review_revision=0 if case.finding_review is None else case.finding_review.revision, correction_parent_revision=case.correction_parent_revision, correction_reviewer_id=case.correction_reviewer_id, image_url=f"/api/v1/datasets/{quote(source.dataset_id, safe='')}/samples/{quote(source.sample_id, safe='')}/assets/image?revision={source.sample_revision}")

class FeedbackPolicyCreate(Record):
    name: Text
    reviewer_id: Identifier
    example_ids: list[ExampleIdentifier] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.example_ids)) != len(self.example_ids):
            raise ValueError('Duplicate examples.')
        return self

class GroundTruthCreate(Record):
    reviewer_id: Identifier
    cases: list[Adjudication] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        units = [(c.audit_id, c.sample_id, c.sample_revision, c.rule_id) for c in self.cases]
        if len(set(units)) != len(units):
            raise ValueError('Duplicate truth units.')
        return self

class FrozenSet(Record):
    resource_id: Identifier
    version: Literal[1] = 1
    name: Text
    reviewer_id: Identifier
    created_at: AwareDatetime
    cases: list[CaseSnapshot] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def scope(self):
        first = self.cases[0]
        if any((c.target != first.target or c.manifest.dataset_id != first.manifest.dataset_id for c in self.cases)):
            raise ValueError('One exact rule and dataset scope is required.')
        identities = [(c.manifest.dataset_id, c.manifest.sample_id) for c in self.cases]
        if len(set(identities)) != len(identities):
            raise ValueError('Duplicate logical samples.')
        if first.target.rule.check_name == 'pedestrian_box_enclosure' and first.finding_id is not None:
            if any(c.model_execution != first.model_execution for c in self.cases):
                raise ValueError('Enclosure examples require one exact execution pin.')
        return self

class FeedbackPolicy(Record):
    policy_id: Identifier
    version: Literal[1] = 1
    name: Text
    strategy: Literal['reviewer_examples'] = 'reviewer_examples'
    status: Literal['draft', 'active']
    snapshot_sha256: Digest
    reviewer_id: Identifier
    created_at: AwareDatetime
    examples: list[CaseView] = Field(min_length=1, max_length=4)
    activated_by: Identifier | None = None
    evaluation_id: Identifier | None = None

    @model_validator(mode='after')
    def activation(self):
        if (self.status == 'active') != (self.activated_by is not None and self.evaluation_id is not None):
            raise ValueError('Activation attribution is incomplete.')
        if self.status == 'draft' and (self.activated_by is not None or self.evaluation_id is not None):
            raise ValueError('Draft policy cannot contain activation attribution.')
        return self

class GroundTruth(Record):
    ground_truth_id: Identifier
    version: Literal[1] = 1
    snapshot_sha256: Digest
    reviewer_id: Identifier
    created_at: AwareDatetime
    cases: list[CaseView] = Field(min_length=1, max_length=100)

class EvidenceLimits(Record):
    max_image_bytes: int = Field(default=8 * 1024 ** 2, ge=1024, le=16 * 1024 ** 2)
    max_annotation_bytes: int = Field(default=131072, ge=1024, le=262144)
    max_pixels: int = Field(default=16000000, ge=1, le=32000000)


class ExecutionPin(EvidenceLimits):
    model: Text
    model_digest: Annotated[str, StringConstraints(pattern='^sha256:[0-9a-f]{64}$')]
    prompt_version: Literal['annotation-guideline-v1', 'annotation-guideline-v2', 'annotation-guideline-v3', 'annotation-guideline-v4', 'pedestrian-box-feedback-v1'] = 'annotation-guideline-v1'
    preprocessing: Literal['original-png-no-transform-v1', 'original-and-annotation-overlay-v1', 'box-context-png-v1'] = 'original-png-no-transform-v1'
    schema_version: Literal[1] = 1
    identity_assurance: Literal['local-server-reported-pre-post-v1'] = 'local-server-reported-pre-post-v1'
    temperature: Literal[0] = 0
    num_ctx: int = Field(default=16384, ge=1024, le=131072)
    num_predict: int = Field(default=512, ge=128, le=2048)
    # Omit automatic selection so old immutable snapshot fingerprints stay valid.
    num_thread: int | None = Field(
        default=None, ge=1, le=1024, exclude_if=lambda value: value is None,
        description="Pinned CPU threads; omitted or null retains automatic selection.",
    )
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    max_request_bytes: int = Field(default=64 * 1024 ** 2, ge=1024, le=128 * 1024 ** 2)
    max_response_bytes: int = Field(default=65536, ge=1024, le=65536)
    enclosure: EnclosureExecutionPin | None = Field(default=None, exclude_if=lambda value: value is None)

    @classmethod
    def for_enclosure(cls, pin: EnclosureExecutionPin) -> 'ExecutionPin':
        return cls(model=pin.model, model_digest=pin.model_digest,
            prompt_version='pedestrian-box-feedback-v1', preprocessing=pin.view_version,
            num_ctx=pin.num_ctx, num_predict=pin.num_predict, num_thread=pin.num_thread,
            timeout_seconds=pin.request_timeout_seconds, max_request_bytes=pin.max_request_bytes,
            max_response_bytes=pin.max_response_bytes, max_image_bytes=pin.max_image_bytes,
            max_annotation_bytes=pin.max_annotation_bytes, max_pixels=pin.max_pixels, enclosure=pin)

    @model_validator(mode='after')
    def method_matches_pin(self):
        if self.enclosure is None:
            expected = {'annotation-guideline-v1': 'original-png-no-transform-v1',
                        'annotation-guideline-v2': 'original-png-no-transform-v1',
                        'annotation-guideline-v3': 'original-and-annotation-overlay-v1',
                        'annotation-guideline-v4': 'original-and-annotation-overlay-v1'}
            if expected.get(self.prompt_version) != self.preprocessing:
                raise ValueError('Prompt and preprocessing versions must match.')
        else:
            pin = self.enclosure
            if (self.prompt_version != 'pedestrian-box-feedback-v1' or self.preprocessing != pin.view_version
                    or any(getattr(self, field) != getattr(pin, field) for field in (
                        'model', 'model_digest', 'num_ctx', 'num_predict', 'num_thread',
                        'max_request_bytes', 'max_response_bytes', 'max_image_bytes', 'max_annotation_bytes', 'max_pixels'))
                    or self.timeout_seconds != pin.request_timeout_seconds):
                raise ValueError('Feedback execution differs from its enclosure pin.')
        return self

class FeedbackRuntime(Record):
    execution: ExecutionPin
    policy: FrozenSet | None = None

class Prediction(Record):
    case_id: Digest
    rule_id: Identifier
    outcome: Outcome
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    evidence_references: list[Literal['image', 'annotations', 'guideline']] = Field(max_length=3)

class RecordedPrediction(Record):
    prediction: Prediction
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    model_verified: bool
    enclosure: EnclosureEvidence | None = Field(default=None, exclude_if=lambda value: value is None)
    visual_assessment: VisualAssessment | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def verified_outcome(self):
        if not self.model_verified and self.prediction.outcome != 'unavailable':
            raise ValueError('Unverified execution cannot publish a substantive proposal.')
        if self.enclosure is not None and self.prediction.outcome != self.enclosure.aggregate.outcome:
            raise ValueError('Prediction differs from its per-box aggregate.')
        if self.visual_assessment is not None and (
                not self.model_verified or self.enclosure is not None
                or self.visual_assessment.case_id != self.prediction.case_id
                or self.visual_assessment.rule_id != self.prediction.rule_id
                or self.visual_assessment.aggregate_outcome() != self.prediction.outcome):
            raise ValueError('Visual observation identity or provenance differs.')
        return self

class CaseComparison(Record):
    case_id: Digest
    baseline: RecordedPrediction
    candidate: RecordedPrediction

    @model_validator(mode='after')
    def identity(self):
        if self.baseline.prediction.case_id != self.case_id or self.candidate.prediction.case_id != self.case_id:
            raise ValueError('Prediction/case mismatch.')
        return self

class ArmMetrics(Record):
    intended: int = Field(ge=1)
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    fn: int = Field(ge=0)
    tn: int = Field(ge=0)
    negative_abstentions: int = Field(ge=0)
    unsure: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    coverage: float = Field(ge=0, le=1, allow_inf_nan=False)
    precision: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    recall: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    f1: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    total_duration_ms: float = Field(ge=0, allow_inf_nan=False)

class ComparisonMetrics(Record):
    baseline: ArmMetrics
    candidate: ArmMetrics
    assessment: Literal['improved', 'equal', 'regressed', 'inconclusive']

class FeedbackEvaluationCreate(Record):
    policy_id: Identifier
    policy_version: Literal[1] = 1
    ground_truth_id: Identifier
    ground_truth_version: Literal[1] = 1
    reviewer_id: Identifier

class EvaluationInput(Record):
    policy: FrozenSet
    truth: FrozenSet
    runtime: ExecutionPin
    requested_by: Identifier

    @model_validator(mode='after')
    def held_out(self):
        if any((c.finding_id is None for c in self.policy.cases)) or any((c.finding_id is not None for c in self.truth.cases)):
            raise ValueError('Policy examples and explicit truth provenance differ.')
        if len(self.policy.cases) > 4 or self.policy.cases[0].target != self.truth.cases[0].target:
            raise ValueError('Evaluation scope is incompatible.')
        if self.policy.cases[0].target.rule.check_name == 'pedestrian_box_enclosure':
            if self.runtime != self.policy.cases[0].model_execution or self.runtime.enclosure is None:
                raise ValueError('Enclosure comparisons must preserve the reviewed execution pin.')
        elif self.runtime.enclosure is not None:
            raise ValueError('Generic comparisons cannot run an enclosure execution.')
        train = self.policy.cases
        test = self.truth.cases
        if train[0].manifest.dataset_id != test[0].manifest.dataset_id:
            raise ValueError('Evaluation dataset scope is incompatible.')
        keys = {(c.manifest.dataset_id, c.manifest.sample_id) for c in train}
        names = {c.manifest.sample_id for c in train}
        hashes = {c.manifest.original_image.sha256 for c in train}
        if any(((c.manifest.dataset_id, c.manifest.sample_id) in keys or c.manifest.sample_id in names or c.manifest.original_image.sha256 in hashes for c in test)):
            raise ValueError('Examples and held-out cases overlap.')
        return self

class FeedbackEvaluation(Record):
    evaluation_id: Identifier
    job_id: Identifier
    policy_id: Identifier
    policy_version: Literal[1] = 1
    policy_sha256: Digest
    ground_truth_id: Identifier
    ground_truth_version: Literal[1] = 1
    truth_sha256: Digest
    execution: ExecutionPin
    status: Literal['queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled']
    created_at: AwareDatetime
    metrics: ComparisonMetrics | None
    results: list[CaseComparison] = Field(max_length=100)

class FeedbackEvaluationStarted(Record):
    evaluation: FeedbackEvaluation
    job: Job

class FeedbackPolicyActivate(Record):
    evaluation_id: Identifier
    expected_policy_version: Literal[1] = 1
    reviewer_id: Identifier
T = TypeVar('T')

class FeedbackPage(Record, Generic[T]):
    items: list[T]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=9223372036854775807)
    total: int = Field(ge=0, le=9223372036854775807)
    next_offset: int | None = Field(ge=0, le=9223372036854775807)

    @model_validator(mode='after')
    def coherent(self):
        size = min(self.limit, max(0, self.total - self.offset))
        expected = self.offset + size if self.offset + size < self.total else None
        if len(self.items) != size or self.next_offset != expected:
            raise ValueError('Page count/items/continuation disagree.')
        return self

class FeedbackExamplePage(FeedbackPage[CaseView]):
    """Stable named collection transport for OpenAPI component references."""

class FeedbackPolicyPage(FeedbackPage[FeedbackPolicy]):
    """Candidate policy collection."""

class FeedbackEvaluationPage(FeedbackPage[FeedbackEvaluation]):
    """Comparison collection, including nonterminal and failed resources."""
for _model in (CaseSnapshot, FrozenSet, FeedbackRuntime, EvaluationInput):
    _model.model_rebuild()
