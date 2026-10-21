"""Immutable reviewer examples/policies/truth, with locked current-review cutoff."""
from ..models.feedback import FeedbackExamplePage, FeedbackPolicyPage, FeedbackEvaluationPage
from contextlib import contextmanager
from uuid import uuid4
import json
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from ..core.config import load_database_settings, load_asset_storage_settings
from ..core.feedback import FeedbackError
from ..models.feedback import Adjudication, CaseReference, CaseSnapshot, CaseView, FeedbackPage, FeedbackPolicy, FeedbackPolicyCreate, FrozenSet, GroundTruth, GroundTruthCreate, RulePin, EvidenceLimits, ExecutionPin, RecordedPrediction, case_view, checked, fingerprint
from .database import database_connection, DatabaseError, CommitOutcomeUnknownError
from .dataset_repository import DatasetRepository, DatasetStorageError, DatasetRevisionNotFoundError, DatasetSampleNotFoundError, DatasetNotFoundError, _pagination, _identifier
from .repository import RulebookRepository, RulebookStorageError
from .workflow_repository import _FINDINGS_WITH_REVIEWS, _decode_reviewed_finding, WorkflowStorageError

class FeedbackRepository:

    def __init__(self, *, settings=None, asset_settings=None):
        self.settings = load_database_settings() if settings is None else settings
        self.asset_settings = load_asset_storage_settings() if asset_settings is None else asset_settings

    @contextmanager
    def connection(self, *, write=False):
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except CommitOutcomeUnknownError as error:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error
        except (DatabaseError, DatasetStorageError, DatasetRevisionNotFoundError, DatasetSampleNotFoundError, DatasetNotFoundError, RulebookStorageError, WorkflowStorageError) as error:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error

    @staticmethod
    def decode(model, document):
        try:
            return model.model_validate_json(json.dumps(document, allow_nan=False))
        except (ValidationError, ValueError, TypeError) as error:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error

    def _case(self, connection, reference, expected, reviewer, reason, *, finding=None):
        row = connection.execute('SELECT * FROM audit_runs WHERE audit_id=%s', (reference.audit_id,)).fetchone()
        if row is None:
            raise FeedbackError('NOT_FOUND')
        if row['source_mode'] != 'snapshot' or row['status'] not in ('completed', 'failed', 'cancelled'):
            raise FeedbackError('FEEDBACK_NOT_READY')
        sample = connection.execute('SELECT * FROM audit_samples WHERE audit_id=%s AND sample_id=%s AND sample_revision=%s', (reference.audit_id, reference.sample_id, reference.sample_revision)).fetchone()
        if sample is None or sample['manifest_dataset_id'] != row['dataset_id']:
            raise FeedbackError('FEEDBACK_NOT_READY')
        bookrow = connection.execute('SELECT * FROM rulebook_versions WHERE rulebook_id=%s AND version=%s', (row['rulebook_id'], row['rulebook_version'])).fetchone()
        if bookrow is None:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        book = RulebookRepository._decode(bookrow)
        rules = [r for r in book.rules if r.rule_id == reference.rule_id]
        if book.confirmation_state.value != 'confirmed' or len(rules) != 1:
            raise FeedbackError('FEEDBACK_NOT_READY')
        try:
            target = RulePin(rulebook_id=book.rulebook_id, rulebook_version=book.version, rule=rules[0])
        except ValidationError as error:
            raise FeedbackError('FEEDBACK_NOT_READY') from error
        publication = DatasetRepository(settings=self.settings)._get_publication(connection, row['dataset_id'], reference.sample_id, reference.sample_revision)
        lineage = connection.execute('SELECT parent_revision,reviewer_id FROM annotation_revision_provenance WHERE dataset_id=%s AND sample_id=%s AND sample_revision=%s', (row['dataset_id'], reference.sample_id, reference.sample_revision)).fetchone()
        if finding is not None:
            if finding.description != target.rule.description or finding.parameters != target.rule.parameters or finding.check_name != 'annotation_guideline' or (finding.rulebook_id != book.rulebook_id) or (finding.rulebook_version != book.version) or (finding.source_sentence != target.rule.source_sentence) or (finding.object_id is not None):
                raise FeedbackError('FEEDBACK_NOT_READY')
            reviewed = finding.review.updated_at
        else:
            reviewed = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        try:
            return CaseSnapshot(reference=reference, target=target, manifest=publication.manifest, expected=expected, reviewer_id=reviewer, reviewed_at=reviewed, reason=reason, finding_id=None if finding is None else finding.finding_id, finding_review=None if finding is None else finding.review, model_execution=None if finding is None else self.decode(ExecutionPin, finding.evidence.measurements.get('execution')), model_proposal=None if finding is None else self.decode(RecordedPrediction, finding.evidence.measurements.get('model_proposal')), correction_parent_revision=None if lineage is None else lineage['parent_revision'], correction_reviewer_id=None if lineage is None else lineage['reviewer_id'])
        except ValidationError as error:
            raise FeedbackError('FEEDBACK_NOT_READY') from error

    def _example(self, connection, row):
        finding = _decode_reviewed_finding(row)
        if finding.outcome.value not in ('pass', 'fail') or finding.review.resolution.value != 'accepted':
            raise FeedbackError('FEEDBACK_NOT_READY')
        case = self._case(connection, CaseReference(audit_id=finding.audit_id, sample_id=finding.sample_id, sample_revision=finding.sample_revision, rule_id=finding.rule_id), finding.outcome.value, finding.review.reviewer_id, 'Human accepted this binary annotation-guideline proposal.', finding=finding)
        from ..models.feedback import unit_id
        proposal = self.decode(RecordedPrediction, finding.evidence.measurements.get('model_proposal'))
        if not proposal.model_verified or proposal.prediction.outcome != finding.outcome.value or proposal.prediction.case_id != unit_id(case.manifest, case.target) or (proposal.prediction.rule_id != case.target.rule.rule_id):
            raise FeedbackError('FEEDBACK_NOT_READY')
        audit = connection.execute('SELECT request FROM audit_runs WHERE audit_id=%s', (case.reference.audit_id,)).fetchone()
        from ..models.audit import BatchAuditRequest
        original = self.decode(BatchAuditRequest, audit['request'])
        if original.feedback_runtime is None or original.feedback_runtime.execution != case.model_execution:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        return case

    def examples(self, *, limit: int=20, offset: int=0) -> FeedbackExamplePage:
        _pagination(limit, offset)
        clause = " WHERE findings.document->>'check_name'='annotation_guideline' AND findings.outcome IN ('pass','fail') AND latest.resolution='accepted' AND findings.document#>>'{evidence,measurements,model_proposal,model_verified}'='true' AND EXISTS (SELECT 1 FROM audit_runs a WHERE a.audit_id=findings.audit_id AND a.source_mode='snapshot' AND a.status IN ('completed','failed','cancelled'))"
        with self.connection() as connection:
            total = connection.execute('SELECT count(*) AS n FROM (' + _FINDINGS_WITH_REVIEWS + clause + ') q').fetchone()['n']
            rows = connection.execute(_FINDINGS_WITH_REVIEWS + clause + ' ORDER BY findings.created_at DESC,findings.finding_id COLLATE "C" LIMIT %s OFFSET %s', (limit, offset)).fetchall()
            items = [case_view(self._example(connection, row)) for row in rows]
        return FeedbackExamplePage(items=items, limit=limit, offset=offset, total=total, next_offset=offset + len(items) if offset + len(items) < total else None)

    def _set(self, connection, identifier, kind):
        _identifier(identifier, 'feedback_id')
        row = connection.execute('SELECT * FROM feedback_sets WHERE resource_id=%s AND kind=%s', (identifier, kind)).fetchone()
        if row is None:
            raise FeedbackError('NOT_FOUND')
        saved = self.decode(FrozenSet, row['document'])
        if saved.resource_id != row['resource_id'] or saved.created_at != row['created_at'] or fingerprint(saved) != row['sha256']:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        if kind == 'policy' and (len(saved.cases) > 4 or any((c.finding_id is None for c in saved.cases))):
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        if kind == 'truth' and any(c.finding_id is not None for c in saved.cases):
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        return saved

    def _put(self, connection, kind, saved):
        connection.execute('INSERT INTO feedback_sets(resource_id,kind,document,sha256,created_at) VALUES(%s,%s,%s,%s,%s)', (saved.resource_id, kind, Jsonb(saved.model_dump(mode='json')), fingerprint(saved), saved.created_at))

    def _verify(self, cases):
        from ..services.feedback_evidence import evidence
        bounds = EvidenceLimits()
        for case in cases:
            evidence(case.manifest, bounds, self.asset_settings)

    def create_policy(self, request: FeedbackPolicyCreate) -> FeedbackPolicy:
        request = checked(FeedbackPolicyCreate, request)
        from base64 import urlsafe_b64decode, urlsafe_b64encode
        import re
        selected = []
        for identifier in sorted(request.example_ids):
            match = re.fullmatch('example_([A-Za-z0-9_-]+)\\.([1-9][0-9]{0,9})', identifier)
            if match is None:
                raise FeedbackError('FEEDBACK_NOT_READY')
            try:
                encoded = match[1]
                finding_id = urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8')
                if urlsafe_b64encode(finding_id.encode()).decode().rstrip('=') != encoded:
                    raise ValueError('Noncanonical identifier.')
            except (ValueError, UnicodeError) as error:
                raise FeedbackError('FEEDBACK_NOT_READY') from error
            selected.append((finding_id, int(match[2])))
        with self.connection() as connection:
            cases = []
            for finding_id, revision in selected:
                row = connection.execute(_FINDINGS_WITH_REVIEWS + ' WHERE findings.finding_id=%s', (finding_id,)).fetchone()
                if row is None:
                    raise FeedbackError('NOT_FOUND')
                if row['current_review_revision'] != revision:
                    raise FeedbackError('FEEDBACK_CONFLICT')
                cases.append(self._example(connection, row))
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        self._verify(cases)
        try:
            saved = FrozenSet(resource_id='policy_' + uuid4().hex, name=request.name, reviewer_id=request.reviewer_id, created_at=now, cases=cases)
        except ValidationError as error:
            raise FeedbackError('POLICY_INCOMPATIBLE') from error
        with self.connection(write=True) as connection:
            for finding_id, revision in sorted(selected):
                connection.execute('SELECT finding_id FROM findings WHERE finding_id=%s FOR UPDATE', (finding_id,)).fetchone()
                row = connection.execute(_FINDINGS_WITH_REVIEWS + ' WHERE findings.finding_id=%s', (finding_id,)).fetchone()
                if row is None or row['current_review_revision'] != revision:
                    raise FeedbackError('FEEDBACK_CONFLICT')
                current = self._example(connection, row)
                old = next((c for c in cases if c.finding_id == finding_id))
                if current != old:
                    raise FeedbackError('FEEDBACK_CONFLICT')
            self._put(connection, 'policy', saved)
        return self.get_policy(saved.resource_id)

    def create_truth(self, request: GroundTruthCreate) -> GroundTruth:
        request = checked(GroundTruthCreate, request)
        with self.connection() as connection:
            cases = [self._case(connection, CaseReference(**c.model_dump(exclude={'expected', 'reason'})), c.expected, request.reviewer_id, c.reason) for c in request.cases]
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        self._verify(cases)
        try:
            saved = FrozenSet(resource_id='truth_' + uuid4().hex, name='Explicit rule-level adjudications', reviewer_id=request.reviewer_id, created_at=now, cases=cases)
        except ValidationError as error:
            raise FeedbackError('POLICY_INCOMPATIBLE') from error
        with self.connection(write=True) as connection:
            self._put(connection, 'truth', saved)
        return self.get_truth(saved.resource_id)

    def _policy_view(self, connection, saved):
        activation = connection.execute('SELECT * FROM feedback_activations WHERE policy_id=%s', (saved.resource_id,)).fetchone()
        return FeedbackPolicy(policy_id=saved.resource_id, name=saved.name, status='draft' if activation is None else 'active', snapshot_sha256=fingerprint(saved), reviewer_id=saved.reviewer_id, created_at=saved.created_at, examples=[case_view(c) for c in saved.cases], activated_by=None if activation is None else activation['reviewer_id'], evaluation_id=None if activation is None else activation['evaluation_id'])

    def get_policy(self, identifier: str) -> FeedbackPolicy:
        with self.connection() as connection:
            return self._policy_view(connection, self._set(connection, identifier, 'policy'))

    def get_truth(self, identifier: str) -> GroundTruth:
        with self.connection() as connection:
            saved = self._set(connection, identifier, 'truth')
        return GroundTruth(ground_truth_id=saved.resource_id, snapshot_sha256=fingerprint(saved), reviewer_id=saved.reviewer_id, created_at=saved.created_at, cases=[case_view(c) for c in saved.cases])

    def policies(self, *, limit: int=20, offset: int=0) -> FeedbackPolicyPage:
        _pagination(limit, offset)
        with self.connection() as connection:
            total = connection.execute("SELECT count(*) AS n FROM feedback_sets WHERE kind='policy'").fetchone()['n']
            rows = connection.execute('SELECT resource_id FROM feedback_sets WHERE kind=\'policy\' ORDER BY created_at DESC,resource_id COLLATE "C" LIMIT %s OFFSET %s', (limit, offset)).fetchall()
            items = [self._policy_view(connection, self._set(connection, r['resource_id'], 'policy')) for r in rows]
        return FeedbackPolicyPage(items=items, limit=limit, offset=offset, total=total, next_offset=offset + len(items) if offset + len(items) < total else None)
