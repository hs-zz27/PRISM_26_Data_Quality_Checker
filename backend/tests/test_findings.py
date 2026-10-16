"""Finding records and their distinction from reviewer decisions."""

from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from backend.app.models.finding import Evidence, Finding, FindingOutcome, FindingResolution, FindingReview
from backend.app.models.rule import RuleOrigin


def make_finding(**changes):
    values = {
        "finding_id": "finding_1", "audit_id": "audit_1", "sample_id": "sample_1",
        "sample_revision": 1, "object_id": 2, "rulebook_id": "book_1", "rulebook_version": 3,
        "rule_id": "labels", "description": "Use an allowed label.", "check_name": "allowed_labels",
        "outcome": "fail", "reason": "label_not_allowed", "origin": "extracted",
        "parameters": {"allowed_labels": ["person"]}, "source_sentence": "Use person labels.",
        "evidence": Evidence(measurements={"actual_label": "car", "allowed_labels": ["person"]}),
    }
    values.update(changes)
    return Finding(**values)


@pytest.mark.parametrize("outcome", list(FindingOutcome))
def test_every_tool_outcome_starts_pending_review(outcome):
    finding = make_finding(outcome=outcome)
    assert finding.outcome is outcome and finding.review.resolution is FindingResolution.PENDING
    assert finding.review.revision == 0
    assert finding.review.reviewer_id is None and finding.review.updated_at is None


def test_json_round_trip_preserves_rule_snapshot_and_evidence():
    finding = make_finding()
    stored = finding.model_dump(mode="json")
    assert stored["outcome"] == "fail" and stored["origin"] == "extracted"
    assert stored["rulebook_version"] == 3 and stored["sample_revision"] == 1
    assert stored["parameters"] == {"allowed_labels": ["person"]}
    assert stored["source_sentence"] == "Use person labels."
    assert stored["evidence"]["measurements"]["actual_label"] == "car"
    restored = Finding.model_validate_json(finding.model_dump_json())
    assert restored == finding and restored.origin is RuleOrigin.EXTRACTED
    assert restored.outcome is FindingOutcome.FAIL


@pytest.mark.parametrize("object_id", [None, 0, -1])
def test_sample_wide_findings_and_invalid_source_object_ids_are_representable(object_id):
    assert make_finding(object_id=object_id).object_id == object_id


def test_unsupported_reviewer_rule_can_have_no_citation_or_measurements():
    finding = make_finding(
        origin="reviewer", source_sentence=None, check_name=None, parameters={},
        outcome="unavailable", reason="unsupported_check", object_id=None, evidence=Evidence(),
    )
    assert finding.outcome is FindingOutcome.UNAVAILABLE and finding.evidence.measurements == {}


def test_relative_asset_links_and_json_measurements_are_preserved():
    path = "/api/v1/datasets/example/samples/image/assets/image?revision=1"
    evidence = Evidence(image_url=path, measurements={"label": "person", "optional": None, "score": 0.5})
    assert evidence.image_url == path and evidence.measurements["optional"] is None


def test_default_review_and_evidence_collections_are_independent():
    values = make_finding().model_dump(exclude={"evidence", "review"})
    first = Finding.model_validate(values)
    second = Finding.model_validate(values)
    first.evidence.measurements["object_count"] = 2
    first.review.comment = "Inspect this object."
    assert second.evidence.measurements == {} and second.review.comment is None


def test_reviewed_read_record_remains_separate_from_tool_outcome():
    timestamp = datetime(2026, 10, 15, 12, tzinfo=UTC)
    finding = make_finding(review=FindingReview(
        revision=1, resolution="rejected", reviewer_id="reviewer_1",
        comment="This label is correct.", updated_at=timestamp,
    ))
    assert finding.outcome is FindingOutcome.FAIL
    assert finding.review.resolution is FindingResolution.REJECTED
    assert Finding.model_validate_json(finding.model_dump_json()) == finding


@pytest.mark.parametrize("resolution", ["accepted", "rejected", "unsure"])
@pytest.mark.parametrize("changes", [
    {"revision": 0}, {"reviewer_id": None}, {"updated_at": None},
    {"revision": 0, "reviewer_id": None, "updated_at": None},
])
def test_non_pending_review_requires_revision_and_attribution(resolution, changes):
    values = {
        "resolution": resolution, "revision": 1, "reviewer_id": "reviewer_1",
        "updated_at": datetime(2026, 10, 16, 12, tzinfo=UTC),
    }
    values.update(changes)
    with pytest.raises(ValidationError, match="positive revision, reviewer ID and timestamp"):
        FindingReview(**values)


@pytest.mark.parametrize("resolution", ["accepted", "rejected", "unsure"])
def test_attributed_review_decisions_round_trip(resolution):
    review = FindingReview(
        resolution=resolution, revision=1, reviewer_id="reviewer_1",
        updated_at=datetime(2026, 10, 16, 12, tzinfo=UTC),
    )
    assert FindingReview.model_validate_json(review.model_dump_json()) == review


@pytest.mark.parametrize("citation", [None, "", " \t"])
def test_extracted_findings_require_a_nonblank_source_sentence(citation):
    with pytest.raises(ValidationError):
        make_finding(source_sentence=citation)


def test_extracted_finding_cannot_omit_its_source_sentence():
    values = make_finding().model_dump(exclude={"source_sentence"})
    with pytest.raises(ValidationError, match="must cite a source sentence"):
        Finding.model_validate(values)


@pytest.mark.parametrize("changes", [
    {"sample_revision": 0}, {"sample_revision": True}, {"rulebook_version": "3"},
    {"rulebook_version": 0}, {"object_id": True}, {"finding_id": " "},
    {"rule_id": ""}, {"outcome": "accepted"}, {"reason": " "}, {"origin": "model"},
    {"parameters": {"labels": {"person"}}}, {"unexpected": "value"},
])
def test_invalid_finding_fields_are_rejected(changes):
    with pytest.raises(ValidationError):
        make_finding(**changes)


def test_review_timestamp_must_have_a_timezone():
    with pytest.raises(ValidationError):
        FindingReview(updated_at=datetime(2026, 10, 15, 12))
