"""The prepared scope is verified against disk and must stay label-free."""

import json

import pytest

from backend.experiments.box_grounding.prepare import write_json
from backend.experiments.box_grounding.scope import ScopeError, changed_since_load, load_scope
from backend.experiments.box_grounding.settings import ExecutionSettings, require_loopback
from backend.experiments.box_grounding.runner import RunError, select_cases

from box_helpers import DIGEST


def edit_scope(corpus, change):
    document = json.loads(corpus.scope_path.read_bytes())
    change(document)
    write_json(corpus.scope_path, document)


def test_a_clean_scope_loads_with_every_case_verified(corpus):
    scope = corpus.scope()
    assert [c.case_name for c in scope.cases] == ["case-02", "case-03", "case-04", "case-06", "case-13"]
    assert [c.case_name for c in scope.development_cases()] == ["case-02", "case-03", "case-04", "case-13"]
    assert scope.rule_id == "VISIBLE-PEDESTRIAN" and scope.guideline.startswith("Each annotation box")


@pytest.mark.parametrize("field", ["expected", "decision", "reviewer_id", "controlled_perturbation", "adjudication", "history"])
def test_a_scope_containing_label_or_review_fields_is_refused(corpus, field):
    edit_scope(corpus, lambda d: d["cases"][0]["inputs"].update({field: "fail"}))
    with pytest.raises(ScopeError, match="label or review field"):
        load_scope(corpus.scope_path)


def test_a_top_level_label_field_is_refused_too(corpus):
    edit_scope(corpus, lambda d: d.update(expected_labels={"case-02": "fail"}) or d.update(decision="x"))
    with pytest.raises(ScopeError):
        load_scope(corpus.scope_path)


@pytest.mark.parametrize("target", ["image.png", "annotation.txt"])
def test_changed_source_bytes_are_detected_on_load_and_after(corpus, target):
    path = corpus.scope_path.parent / "case-03" / target
    original = path.read_bytes()
    scope = corpus.scope()
    assert changed_since_load(scope) == []
    path.write_bytes(original + b"x")
    assert changed_since_load(scope) == ["case-03"]
    with pytest.raises(ScopeError, match="differ from the scope hash"):
        load_scope(corpus.scope_path)


def test_a_tampered_case_identity_is_refused(corpus):
    edit_scope(corpus, lambda d: d["cases"][1].update(case_id="0" * 64))
    with pytest.raises(ScopeError, match="identity"):
        load_scope(corpus.scope_path)


def test_paths_cannot_leave_the_prepared_directory(corpus):
    edit_scope(corpus, lambda d: d["cases"][0].update(image="../dataset/PNGImages/case-02.png"))
    with pytest.raises(ScopeError, match="leaves the prepared directory"):
        load_scope(corpus.scope_path)


@pytest.mark.parametrize("change,message", [
    (lambda d: d.update(schema_version=2), "schema version"),
    (lambda d: d.update(cases=[]), "no cases"),
    (lambda d: d["cases"].append(dict(d["cases"][0])), "unique"),
])
def test_structural_problems_are_refused(corpus, change, message):
    edit_scope(corpus, change)
    with pytest.raises(ScopeError, match=message):
        load_scope(corpus.scope_path)


def test_only_distinct_development_cases_can_be_selected(corpus):
    scope = corpus.scope()
    assert [c.case_name for c in select_cases(scope, None)] == ["case-02", "case-03", "case-04", "case-13"]
    assert [c.case_name for c in select_cases(scope, ("case-04", "case-02"))] == ["case-02", "case-04"]
    for names in (("case-06",), ("case-99",), ("case-02", "case-02")):
        with pytest.raises(RunError):
            select_cases(scope, names)  # training, unknown and duplicate selections are all refused


@pytest.mark.parametrize("host", ["http://127.0.0.1:11434", "http://localhost:11434", "https://[::1]:11434"])
def test_loopback_hosts_are_accepted(host):
    assert require_loopback(host) == host


@pytest.mark.parametrize("host", ["http://192.168.1.5:11434", "http://example.com", "http://user:pw@127.0.0.1:11434",
                                  "http://127.0.0.1:11434/api", "http://127.0.0.1:0", "ftp://127.0.0.1", "127.0.0.1"])
def test_non_local_or_credentialed_hosts_are_refused(host):
    with pytest.raises(ValueError):
        require_loopback(host)
    with pytest.raises(ValueError):
        ExecutionSettings(model_digest=DIGEST, host=host)


@pytest.mark.parametrize("changes", [{"num_ctx": 512}, {"num_predict": 64}, {"timeout_seconds": 0}, {"temperature": 1},
                                     {"model_digest": "sha256:short"}, {"num_thread": 0}])
def test_execution_settings_are_bounded(changes):
    with pytest.raises(ValueError):
        ExecutionSettings(**{"model_digest": DIGEST, **changes})
