"""Synthetic assets/records. Nothing executes until an explicitly invoked test."""
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from PIL import Image
from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetManifestEntry, SampleAssetManifest
from backend.app.models.feedback import CaseReference, CaseSnapshot, RulePin, ExecutionPin, Prediction, RecordedPrediction, CaseComparison, FrozenSet, EvaluationInput, unit_id
from backend.app.models.finding import FindingReview
from backend.app.models.rule import Rule, Rulebook
from backend.app.models.correction import CorrectionObject
from backend.app.services.corrections import annotation_bytes
NOW = datetime(2026, 10, 21, tzinfo=UTC)

def book():
    text = 'Annotation labels agree with the visible scene.'
    rule = Rule(rule_id='SCENE-1', description=text, check_name='annotation_guideline', parameters={}, origin='reviewer', source_sentence=None, confirmation_state='confirmed')
    return Rulebook(rulebook_id='feedback_book', version=1, guideline_text=text, rules=[rule], confirmation_state='confirmed')

def assets(tmp_path):
    root = tmp_path / 'feedback-assets'
    root.mkdir(exist_ok=True, parents=True)
    return AssetStorageSettings(root=root)

def case(tmp_path, name='sample_a', *, expected='fail', approved=False, revision=1):
    settings = assets(tmp_path)
    rulebook = book()
    target = RulePin(rulebook_id=rulebook.rulebook_id, rulebook_version=1, rule=rulebook.rules[0])
    picture = Image.new('RGB', (4, 3), tuple(bytes.fromhex(sha256(name.encode()).hexdigest()[:6])))
    output = BytesIO()
    picture.save(output, format='PNG')
    annotation = annotation_bytes([CorrectionObject(object_id=1, label='person', bbox=(1, 1, 4, 3))], (4, 3), 3, 1024)
    entries = {}
    for role, raw in [('original_image', output.getvalue()), ('annotation_text', annotation)]:
        key = f'{name}/{revision}/{role}'
        path = settings.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        entries[role] = AssetManifestEntry(availability='available', storage_key=key, content_type='text/plain' if role == 'annotation_text' else 'image/png', size_bytes=len(raw), sha256=sha256(raw).hexdigest())
    manifest = SampleAssetManifest(dataset_id='dataset_a', sample_id=name, sample_revision=revision, raw_mask=AssetManifestEntry(availability='missing'), **entries)
    proposal = record(manifest, target, expected)
    return CaseSnapshot(reference=CaseReference(audit_id='audit_' + name, sample_id=name, sample_revision=revision, rule_id=target.rule.rule_id), target=target, manifest=manifest, expected=expected, reviewer_id='human', reviewed_at=NOW, reason='Explicit human adjudication.', finding_id='finding_' + name if approved else None, finding_review=FindingReview(revision=1, resolution='accepted', reviewer_id='human', updated_at=NOW) if approved else None, model_execution=ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64) if approved else None, model_proposal=proposal if approved else None)

def record(manifest, target, outcome='fail', *, verified=True):
    return RecordedPrediction(prediction=Prediction(case_id=unit_id(manifest, target), rule_id=target.rule.rule_id, outcome=outcome, reason='Synthetic supplied-evidence result.', evidence_references=['image', 'annotations']), duration_ms=1.0, model_verified=verified)

def inputs(tmp_path, *, expected=('fail', 'pass')):
    training = case(tmp_path, 'training', approved=True)
    truth = [case(tmp_path, 'heldout_' + str(i), expected=value) for i, value in enumerate(expected)]
    return EvaluationInput(policy=FrozenSet(resource_id='policy_a', name='Small reviewed policy', reviewer_id='human', created_at=NOW, cases=[training]), truth=FrozenSet(resource_id='truth_a', name='Explicit truth', reviewer_id='human', created_at=NOW, cases=truth), runtime=ExecutionPin(model='test-vision', model_digest='sha256:' + 'a' * 64, num_ctx=131072), requested_by='human')

def comparison(case, baseline='pass', candidate='fail', *, verified=True):
    return CaseComparison(case_id=unit_id(case.manifest, case.target), baseline=record(case.manifest, case.target, baseline, verified=verified), candidate=record(case.manifest, case.target, candidate, verified=verified))
