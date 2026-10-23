"""PostgreSQL reads of an audit's findings: filters, paging, reviews and snapshot consistency."""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.app.core.config import DatabaseSettings
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.audit_read import FindingPage
from backend.app.models.finding import Finding, FindingOutcome
from backend.app.models.review import FindingReviewRequest
from backend.app.services.review import review_finding
from backend.app.storage import AuditRepository, FindingRepository
from backend.app.storage.audit_read_repository import AuditReadRepository
from backend.app.storage.database import database_connection, initialize_database
from backend.app.storage.workflow_repository import AuditNotFoundError

MAX_OFFSET = 9_223_372_036_854_775_807


@pytest.fixture
def settings():
    conninfo = os.environ.get("PRISM_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Set PRISM_TEST_DATABASE_URL to run PostgreSQL integration tests.")
    schema = f"prism_audit_findings_{uuid4().hex}"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        settings = DatabaseSettings(conninfo=SecretStr(make_conninfo(conninfo, options=f"-c search_path={schema}")))
        initialize_database(settings)
        yield settings
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _finding(finding_id, audit_id, sample_id, revision, outcome, **changes):
    values = dict(
        finding_id=finding_id, audit_id=audit_id, sample_id=sample_id, sample_revision=revision,
        rulebook_id="book_1", rulebook_version=3, rule_id=f"rule_{finding_id}",
        description="Check labels.", check_name="allowed_labels", outcome=outcome,
        origin="reviewer", parameters={"allowed_labels": ["person"]},
        evidence={"measurements": {"label": "person", "finding": finding_id}},
    )
    return Finding(**(values | changes))


def _audit(audits, audit_id, job_id, samples):
    """Run one audit to completion. samples maps sample_id to (revision, findings)."""
    audits.start_audit(BatchAuditRequest(
        dataset_id=f"dataset_{audit_id}", dataset_root="unused-root", rulebook_id="book_1",
        rulebook_version=3, sample_ids=list(samples),
        sample_revisions={name: revision for name, (revision, _) in samples.items()},
    ), audit_id=audit_id, job_id=job_id)
    job = audits.claim_audit(audit_id, worker_id=f"worker_{audit_id}")
    for sample_id, (_, findings) in samples.items():
        claim = audits.claim_next_sample(job)
        assert claim.sample_id == sample_id
        audits.persist_sample_success(claim, measurements={
            "image_id": sample_id, "review_status": "pending_review", "status": "completed",
        }, findings=findings)


@pytest.fixture
def offline_repository():
    """A repository with no reachable database: invalid arguments must fail before any connection."""
    return AuditReadRepository(
        settings=DatabaseSettings(conninfo=SecretStr("postgresql://nobody:unused@127.0.0.1:1/none")),
    )


@pytest.fixture
def repository(settings):
    audits = AuditRepository(settings=settings)
    _audit(audits, "audit_1", "job_1", {
        "alpha": (2, [_finding("f01", "audit_1", "alpha", 2, "pass"), _finding("f02", "audit_1", "alpha", 2, "fail")]),
        "beta": (5, [
            _finding("f03", "audit_1", "beta", 5, "unsure"),
            _finding("f04", "audit_1", "beta", 5, "unavailable"),
            _finding("f05", "audit_1", "beta", 5, "fail"),
        ]),
        "gamma": (1, []),
    })
    # The same sample ID in another audit must never leak into audit_1.
    _audit(audits, "audit_2", "job_2", {"alpha": (1, [_finding("g01", "audit_2", "alpha", 1, "fail")])})
    _audit(audits, "audit_empty", "job_empty", {"only": (1, [])})
    return AuditReadRepository(settings=settings)


def _ids(page):
    return [item.finding_id for item in page.items]


def _decide(settings, finding_id, resolution, version, comment="Inspected."):
    return review_finding(finding_id, FindingReviewRequest(
        expected_review_version=version, resolution=resolution, reviewer_id="reviewer_1", comment=comment,
    ), repository=FindingRepository(settings=settings))


# --- existence ------------------------------------------------------------------------------

def test_a_missing_audit_is_not_found_but_an_existing_audit_without_findings_is_an_empty_page(repository):
    for filters in ({}, {"sample_id": "alpha"}, {"outcome": "fail"}):
        with pytest.raises(AuditNotFoundError):
            repository.list_audit_findings("no_such_audit", **filters)
    empty = repository.list_audit_findings("audit_empty")
    assert empty == FindingPage(items=[], limit=20, offset=0, total=0, next_offset=None)
    assert repository.list_audit_findings("audit_empty", outcome="fail").total == 0


# --- filters ----------------------------------------------------------------------------------

def test_omitted_filters_return_every_outcome_in_finding_id_order(repository):
    page = repository.list_audit_findings("audit_1")
    assert _ids(page) == ["f01", "f02", "f03", "f04", "f05"]
    assert {item.outcome for item in page.items} == set(FindingOutcome)
    assert (page.limit, page.offset, page.total, page.next_offset) == (20, 0, 5, None)


@pytest.mark.parametrize("outcome,expected", [
    ("pass", ["f01"]), ("fail", ["f02", "f05"]), ("unsure", ["f03"]), ("unavailable", ["f04"]),
])
def test_each_outcome_filter_selects_only_that_machine_outcome(repository, outcome, expected):
    for value in (outcome, FindingOutcome(outcome)):
        page = repository.list_audit_findings("audit_1", outcome=value)
        assert _ids(page) == expected and page.total == len(expected)


def test_sample_and_outcome_filters_apply_individually_and_together(repository):
    assert _ids(repository.list_audit_findings("audit_1", sample_id="alpha")) == ["f01", "f02"]
    assert _ids(repository.list_audit_findings("audit_1", sample_id="beta")) == ["f03", "f04", "f05"]
    assert _ids(repository.list_audit_findings("audit_1", sample_id="beta", outcome="fail")) == ["f05"]
    assert repository.list_audit_findings("audit_1", sample_id="alpha", outcome="unsure").total == 0


def test_a_sample_filter_that_matches_nothing_is_an_empty_page_even_for_an_unknown_sample(repository):
    for sample_id in ("gamma", "no_such_sample"):  # gamma exists but has no findings
        page = repository.list_audit_findings("audit_1", sample_id=sample_id)
        assert page.items == [] and page.total == 0 and page.next_offset is None


def test_totals_count_the_filtered_set_before_paging(repository):
    page = repository.list_audit_findings("audit_1", outcome="fail", limit=1)
    assert _ids(page) == ["f02"] and page.total == 2 and page.next_offset == 1
    page = repository.list_audit_findings("audit_1", sample_id="beta", limit=2)
    assert page.total == 3 and _ids(page) == ["f03", "f04"]


# --- paging -------------------------------------------------------------------------------------

def test_pages_follow_next_offset_to_an_exact_final_page(repository):
    first = repository.list_audit_findings("audit_1", limit=2)
    second = repository.list_audit_findings("audit_1", limit=2, offset=first.next_offset)
    third = repository.list_audit_findings("audit_1", limit=2, offset=second.next_offset)
    assert [_ids(first), _ids(second), _ids(third)] == [["f01", "f02"], ["f03", "f04"], ["f05"]]
    assert (first.next_offset, second.next_offset, third.next_offset) == (2, 4, None)
    assert first.total == second.total == third.total == 5


def test_a_page_that_ends_exactly_at_the_total_has_no_next_offset(repository):
    page = repository.list_audit_findings("audit_1", limit=5)
    assert len(page.items) == 5 and page.next_offset is None
    page = repository.list_audit_findings("audit_1", limit=3, offset=2)
    assert _ids(page) == ["f03", "f04", "f05"] and page.next_offset is None


@pytest.mark.parametrize("offset", [5, 6, 1_000, MAX_OFFSET])
def test_an_offset_past_the_end_is_an_empty_page_with_the_real_total(repository, offset):
    page = repository.list_audit_findings("audit_1", offset=offset)
    assert page.items == [] and page.total == 5 and page.next_offset is None and page.offset == offset


def test_walking_every_page_returns_each_finding_once_in_a_stable_order(repository):
    for size in (1, 2, 3, 100):
        seen, offset = [], 0
        while offset is not None:
            page = repository.list_audit_findings("audit_1", limit=size, offset=offset)
            seen.extend(_ids(page))
            offset = page.next_offset
        assert seen == ["f01", "f02", "f03", "f04", "f05"]
    assert _ids(repository.list_audit_findings("audit_1")) == _ids(repository.list_audit_findings("audit_1"))


# --- isolation and pinned evidence ------------------------------------------------------------------

def test_findings_of_other_audits_are_excluded(repository):
    assert _ids(repository.list_audit_findings("audit_2")) == ["g01"]
    assert "g01" not in _ids(repository.list_audit_findings("audit_1"))
    assert _ids(repository.list_audit_findings("audit_2", sample_id="alpha")) == ["g01"]
    assert repository.list_audit_findings("audit_2", outcome="pass").total == 0


def test_findings_keep_the_audited_sample_revision_and_stored_evidence(repository):
    items = {item.finding_id: item for item in repository.list_audit_findings("audit_1").items}
    assert (items["f01"].sample_revision, items["f03"].sample_revision) == (2, 5)
    assert repository.list_audit_findings("audit_2").items[0].sample_revision == 1
    stored = items["f03"]
    assert (stored.rulebook_id, stored.rulebook_version, stored.rule_id) == ("book_1", 3, "rule_f03")
    assert stored.parameters == {"allowed_labels": ["person"]}
    assert stored.evidence.measurements == {"label": "person", "finding": "f03"}


# --- human reviews ------------------------------------------------------------------------------------

def test_the_latest_human_review_is_returned_and_machine_evidence_is_unchanged(repository, settings):
    before = {item.finding_id: item for item in repository.list_audit_findings("audit_1").items}
    assert {item.review.resolution.value for item in before.values()} == {"pending"}
    _decide(settings, "f02", "accepted", 0)
    _decide(settings, "f02", "rejected", 1, comment="Changed my mind.")
    _decide(settings, "f03", "unsure", 0)

    page = repository.list_audit_findings("audit_1")
    after = {item.finding_id: item for item in page.items}
    assert (after["f02"].review.revision, after["f02"].review.resolution.value) == (2, "rejected")
    assert after["f02"].review.comment == "Changed my mind." and after["f02"].review.reviewer_id == "reviewer_1"
    assert (after["f03"].review.revision, after["f03"].review.resolution.value) == (1, "unsure")
    assert after["f01"].review == before["f01"].review
    for finding_id, finding in after.items():
        assert finding.model_dump(exclude={"review"}) == before[finding_id].model_dump(exclude={"review"})
    stored = FindingRepository(settings=settings).get_finding("f02")
    assert after["f02"] == stored  # one latest-review projection everywhere


def test_filters_follow_the_machine_outcome_not_the_reviewers_decision(repository, settings):
    _decide(settings, "f02", "rejected", 0)
    assert _ids(repository.list_audit_findings("audit_1", outcome="fail")) == ["f02", "f05"]
    assert repository.list_audit_findings("audit_1", outcome="fail").items[0].outcome is FindingOutcome.FAIL


# --- one snapshot ---------------------------------------------------------------------------------------

def test_existence_total_page_and_reviews_come_from_one_snapshot(repository, settings, monkeypatch):
    original = repository._get_audit

    def change_data_after_the_snapshot_starts(connection, audit_id):
        row = original(connection, audit_id)
        _decide(settings, "f02", "accepted", 0)
        FindingRepository(settings=settings).save_findings([_finding("f06", "audit_1", "alpha", 2, "fail")])
        return row

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_get_audit", change_data_after_the_snapshot_starts)
        during = repository.list_audit_findings("audit_1")
    assert during.total == 5 and "f06" not in _ids(during)
    assert {item.review.resolution.value for item in during.items} == {"pending"}

    after = repository.list_audit_findings("audit_1")
    assert after.total == 6 and "f06" in _ids(after)
    assert next(item for item in after.items if item.finding_id == "f02").review.resolution.value == "accepted"


# --- arguments ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("arguments", [
    {"audit_id": ""}, {"audit_id": "   "}, {"audit_id": None}, {"audit_id": 5},
    {"audit_id": "\x00"}, {"audit_id": "\x00audit_1"}, {"audit_id": "audit\x00_1"},
    {"audit_id": "audit_1\x00"}, {"audit_id": "audit_1\x00\n"},
    {"sample_id": ""}, {"sample_id": "  "}, {"sample_id": 5},
    {"sample_id": "\x00"}, {"sample_id": "\x00sample_1"}, {"sample_id": "sample\x00_1"},
    {"sample_id": "sample_1\x00"}, {"sample_id": "sample_1\x00\n"},
    {"outcome": "failed"}, {"outcome": ""}, {"outcome": 5}, {"outcome": True},
    {"limit": 0}, {"limit": 101}, {"limit": -1}, {"limit": "5"}, {"limit": 5.0}, {"limit": True}, {"limit": None},
    {"offset": -1}, {"offset": MAX_OFFSET + 1}, {"offset": "0"}, {"offset": 1.5}, {"offset": True}, {"offset": None},
])
def test_invalid_direct_arguments_are_rejected_before_any_query(offline_repository, monkeypatch, arguments):
    def refuse(*args, **kwargs):
        raise AssertionError("a connection must not be opened for invalid arguments")

    monkeypatch.setattr(offline_repository, "_connection", refuse)
    with pytest.raises(ValueError):
        offline_repository.list_audit_findings(**({"audit_id": "audit_1"} | arguments))


# --- SQL --------------------------------------------------------------------------------------------------------------

class _Recorder:
    def __init__(self, connection, log):
        self._connection, self._log = connection, log

    def execute(self, query, parameters=None):
        self._log.append((str(query), parameters))
        return self._connection.execute(query, parameters)


@pytest.fixture
def recorded(repository, monkeypatch):
    """Record every statement and whether each transaction was a write."""
    log, writes = [], []
    original = repository._connection

    @contextmanager
    def wrapped(*, write=False):
        writes.append(write)
        with original(write=write) as connection:
            yield _Recorder(connection, log)

    monkeypatch.setattr(repository, "_connection", wrapped)
    return log, writes


def test_user_values_are_bound_parameters_never_part_of_the_sql_text(repository, recorded):
    log, writes = recorded
    hostile = "alpha' OR '1'='1"
    page = repository.list_audit_findings("audit_1", sample_id=hostile, outcome="fail", limit=7, offset=3)
    assert page.items == [] and page.total == 0
    assert writes == [False]  # one read-only transaction
    findings_queries = [(query, values) for query, values in log if "findings" in query]
    assert len(findings_queries) == 2  # the count and the page; no per-finding review queries
    for query, values in log:
        assert hostile not in query and "audit_1" not in query and "alpha" not in query
        assert "'fail'" not in query and "'audit_1'" not in query
    assert findings_queries[0][1] == ["audit_1", hostile, "fail"]
    assert findings_queries[1][1] == ["audit_1", hostile, "fail", 7, 3]
    for query, _ in findings_queries:
        assert "LIMIT" not in query or "LIMIT %s OFFSET %s" in query


def test_hostile_values_cannot_widen_or_damage_the_read(repository):
    assert repository.list_audit_findings("audit_1", sample_id="alpha'; DROP TABLE findings; --").total == 0
    assert repository.list_audit_findings("audit_1", sample_id="x' OR '1'='1").items == []
    with pytest.raises(AuditNotFoundError):
        repository.list_audit_findings("audit_1' OR '1'='1")
    assert repository.list_audit_findings("audit_1").total == 5  # the table is intact


def test_filtering_counting_and_paging_happen_in_postgresql(repository, recorded):
    log, _ = recorded
    repository.list_audit_findings("audit_1", outcome="fail", limit=1, offset=1)
    count = next(query for query, _ in log if "count(*)" in query)
    page = next(query for query, _ in log if "ORDER BY" in query)
    assert "findings.outcome = %s" in count and "findings.audit_id = %s" in count
    assert "findings.outcome = %s" in page and "ORDER BY findings.finding_id LIMIT %s OFFSET %s" in page
    assert "LEFT JOIN LATERAL" in page  # latest reviews join in the same statement


def test_reading_findings_writes_nothing(repository, settings):
    def counts():
        with database_connection(settings) as connection:
            return tuple(
                connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                for table in ("findings", "finding_reviews", "audit_runs", "audit_samples", "jobs", "audit_measurements")
            )

    before = counts()
    repository.list_audit_findings("audit_1", limit=2)
    repository.list_audit_findings("audit_1", sample_id="nothing")
    assert counts() == before


def test_the_existing_finding_list_keeps_its_return_type_and_callers(repository, settings):
    listed = FindingRepository(settings=settings).list_findings("audit_1", limit=100)
    assert isinstance(listed, list) and [item.finding_id for item in listed] == ["f01", "f02", "f03", "f04", "f05"]
    assert listed == repository.list_audit_findings("audit_1", limit=100).items
