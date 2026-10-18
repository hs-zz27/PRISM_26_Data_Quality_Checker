"""Sample decisions, attribution and separate revision guards without a database."""

from datetime import UTC, datetime
from unittest.mock import Mock

import psycopg
from pydantic import SecretStr, ValidationError
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.review import SampleReview, SampleReviewDecision, SampleReviewRequest
from backend.app.services.review import review_sample
from backend.app.storage import SampleReviewConflictError, SampleReviewRepository


def request(**changes):
    values = dict(expected_sample_revision=2, expected_review_version=0, decision="accepted",
                  reviewer_id="reviewer_1", comment="")
    return SampleReviewRequest(**(values | changes))


@pytest.fixture
def repository():
    repository = Mock(spec_set=SampleReviewRepository)
    repository.save_sample_review.side_effect = lambda review, **kwargs: review.model_copy(deep=True)
    return repository


@pytest.mark.parametrize("decision", list(SampleReviewDecision))
def test_service_pins_sample_and_attributes_every_decision_including_reopening(repository, decision):
    submission = request(expected_review_version=4, decision=decision, comment="Inspected the annotation.")
    before = datetime.now(UTC)

    result = review_sample("audit_1", "sample_1", submission, repository=repository)

    assert result.audit_id == "audit_1" and result.sample_id == "sample_1"
    assert result.sample_revision == 2 and result.revision == 5
    assert result.decision is decision
    assert result.reviewer_id == submission.reviewer_id and result.comment == submission.comment
    assert before <= result.updated_at <= datetime.now(UTC)
    repository.save_sample_review.assert_called_once_with(result, expected_review_version=4)
    assert submission.expected_review_version == 4 and submission.expected_sample_revision == 2


@pytest.mark.parametrize("changes", [
    {"expected_sample_revision": 0}, {"expected_sample_revision": True}, {"expected_sample_revision": 1.0},
    {"expected_review_version": -1}, {"expected_review_version": True}, {"expected_review_version": 1.0},
    {"decision": "unsure"}, {"reviewer_id": " \n "}, {"comment": None},
    {"revision": 1}, {"sample_revision": 2}, {"audit_id": "audit_1"},
    {"updated_at": "2026-10-18T12:00:00Z"}, {"outcome": "pass"}, {"evidence": {}},
])
def test_request_rejects_invalid_decisions_and_server_owned_fields(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("field,value", [
    ("reviewer_id", " "), ("expected_sample_revision", 0), ("expected_review_version", -1), ("decision", "unsure"),
])
def test_mutated_requests_are_revalidated_before_saving(repository, field, value):
    submission = request()
    setattr(submission, field, value)
    with pytest.raises(ValidationError):
        review_sample("audit_1", "sample_1", submission, repository=repository)
    repository.save_sample_review.assert_not_called()


def test_service_requires_typed_requests_and_does_not_retry_conflicts(repository):
    with pytest.raises(TypeError, match="SampleReviewRequest"):
        review_sample("audit_1", "sample_1", request().model_dump(), repository=repository)
    repository.save_sample_review.assert_not_called()
    conflict = SampleReviewConflictError("audit_1", "sample_1", 2, 0, 1)
    repository.save_sample_review.side_effect = conflict
    with pytest.raises(SampleReviewConflictError) as caught:
        review_sample("audit_1", "sample_1", request(), repository=repository)
    assert caught.value is conflict
    assert repository.save_sample_review.call_count == 1


@pytest.mark.parametrize("changes", [
    {"decision": "accepted"}, {"reviewer_id": "reviewer_1"}, {"comment": ""},
    {"revision": 1},
    {"revision": 1, "decision": "pending_review", "reviewer_id": "reviewer_1", "comment": ""},
])
def test_initial_and_saved_review_states_cannot_hide_missing_attribution(changes):
    with pytest.raises(ValidationError):
        SampleReview(audit_id="audit_1", sample_id="sample_1", sample_revision=2, **changes)


@pytest.fixture
def unconnected_repository(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid review input must be rejected before connecting.")
    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    return SampleReviewRepository(settings=DatabaseSettings(
        conninfo=SecretStr("host=127.0.0.1 dbname=unconnected"),
    ))


@pytest.mark.parametrize("changes,expected_review_version", [
    ({}, True), ({}, -1), ({"revision": 2}, 0), ({"sample_revision": True}, 0),
    ({"decision": "unsure"}, 0), ({"reviewer_id": " "}, 0),
    ({"comment": None}, 0), ({"updated_at": None}, 0), ({"audit_id": " "}, 0),
])
def test_repository_revalidates_mutable_reviews(unconnected_repository, changes, expected_review_version):
    review = SampleReview(
        audit_id="audit_1", sample_id="sample_1", sample_revision=2,
        revision=1, decision="accepted", reviewer_id="reviewer_1", comment="", updated_at=datetime.now(UTC),
    )
    for field, value in changes.items():
        setattr(review, field, value)
    with pytest.raises(ValueError):
        unconnected_repository.save_sample_review(review, expected_review_version=expected_review_version)


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": False},
])
def test_history_pagination_is_validated_before_connecting(unconnected_repository, options):
    with pytest.raises(ValueError):
        unconnected_repository.list_sample_reviews("audit_1", "sample_1", 2, **options)


@pytest.mark.parametrize("identity", [(" ", "sample_1", 2), ("audit_1", "", 2), ("audit_1", "sample_1", True)])
def test_read_identity_is_validated_before_connecting(unconnected_repository, identity):
    with pytest.raises(ValueError):
        unconnected_repository.get_sample_review(*identity)
    with pytest.raises(ValueError):
        unconnected_repository.list_sample_reviews(*identity)
