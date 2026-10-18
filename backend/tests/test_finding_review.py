"""Finding review validation and attribution without database connections."""

from datetime import UTC, datetime
from unittest.mock import Mock

import psycopg
from pydantic import SecretStr, ValidationError
import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.models.finding import FindingResolution, FindingReview
from backend.app.models.review import FindingReviewRequest
from backend.app.services.review import review_finding
from backend.app.storage import FindingRepository, FindingReviewConflictError


def request(**changes):
    values = dict(expected_review_version=0, resolution="accepted", reviewer_id="reviewer_1", comment="")
    return FindingReviewRequest(**(values | changes))


@pytest.fixture
def repository():
    repository = Mock(spec_set=FindingRepository)
    repository.save_finding_review.side_effect = (
        lambda finding_id, review, **kwargs: review.model_copy(deep=True)
    )
    return repository


@pytest.mark.parametrize("resolution", ["accepted", "rejected", "unsure"])
def test_service_assigns_revision_and_timestamp_and_preserves_attribution(repository, resolution):
    submission = request(expected_review_version=3, resolution=resolution, comment="Reviewed the original mask.")
    before = datetime.now(UTC)

    result = review_finding("finding_1", submission, repository=repository)

    assert result.revision == 4
    assert result.resolution is FindingResolution(resolution)
    assert result.reviewer_id == submission.reviewer_id
    assert result.comment == submission.comment
    assert before <= result.updated_at <= datetime.now(UTC)
    repository.save_finding_review.assert_called_once_with("finding_1", result, expected_review_version=3)
    assert submission.expected_review_version == 3


@pytest.mark.parametrize("changes", [
    {"expected_review_version": -1}, {"expected_review_version": True}, {"expected_review_version": 1.0},
    {"resolution": "pending"}, {"resolution": "automatic"}, {"reviewer_id": " \n "},
    {"comment": None}, {"revision": 1}, {"updated_at": "2026-10-18T12:00:00Z"},
    {"outcome": "pass"}, {"evidence": {}},
])
def test_request_rejects_invalid_decisions_and_server_owned_fields(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("field,value", [("reviewer_id", " "), ("expected_review_version", -1), ("resolution", "pending")])
def test_mutated_requests_are_revalidated_before_saving(repository, field, value):
    submission = request()
    setattr(submission, field, value)

    with pytest.raises(ValidationError):
        review_finding("finding_1", submission, repository=repository)

    repository.save_finding_review.assert_not_called()


def test_service_requires_a_typed_request(repository):
    with pytest.raises(TypeError, match="FindingReviewRequest"):
        review_finding("finding_1", request().model_dump(), repository=repository)
    repository.save_finding_review.assert_not_called()


def test_stale_decisions_are_not_automatically_retried(repository):
    conflict = FindingReviewConflictError("finding_1", 0, 1)
    repository.save_finding_review.side_effect = conflict

    with pytest.raises(FindingReviewConflictError) as caught:
        review_finding("finding_1", request(), repository=repository)

    assert caught.value is conflict
    assert repository.save_finding_review.call_count == 1


@pytest.fixture
def unconnected_repository(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid review input must be rejected before connecting.")

    monkeypatch.setattr(psycopg, "connect", unexpected_connection)
    return FindingRepository(settings=DatabaseSettings(
        conninfo=SecretStr("host=127.0.0.1 dbname=unconnected"),
    ))


@pytest.mark.parametrize("changes,expected_review_version", [
    ({}, True), ({}, -1), ({"revision": 2}, 0),
    ({"resolution": "pending"}, 0), ({"reviewer_id": " "}, 0),
    ({"comment": None}, 0), ({"updated_at": None}, 0),
])
def test_repository_revalidates_mutable_reviews(unconnected_repository, changes, expected_review_version):
    review = FindingReview(
        revision=1, resolution="accepted", reviewer_id="reviewer_1", comment="",
        updated_at=datetime.now(UTC),
    )
    for field, value in changes.items():
        setattr(review, field, value)

    with pytest.raises(ValueError):
        unconnected_repository.save_finding_review("finding_1", review, expected_review_version=expected_review_version)


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": False}])
def test_history_pagination_is_validated_before_connecting(unconnected_repository, options):
    with pytest.raises(ValueError):
        unconnected_repository.list_finding_reviews("finding_1", **options)
