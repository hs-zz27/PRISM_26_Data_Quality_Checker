"""Opt-in enclosure check: settings, acceptance pin, per-sample augmentation and worker wiring.

Offline: synthetic images and a fake local Ollama over httpx.MockTransport. No model, database or network.
"""

from io import BytesIO
import json
import pickle
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from backend.app.checks.registry import get_check_definition, validate_check_parameters
from backend.app.core.enclosure import EnclosureConfigurationError, load_enclosure_runtime
from backend.app.core.feedback import FeedbackError
from backend.app.models.audit import BatchAuditRequest
from backend.app.models.finding import FindingOutcome
from backend.app.models.rule import Rule
from backend.app.services import sample_worker
from backend.app.services.enclosure import augment_enclosure, prepare_enclosure_runtime
from backend.app.services.enclosure_prompt import INSTRUCTION_SHA256, SYSTEM_PROMPT_SHA256
from backend.app.services.evaluation import RuleEvaluationResult, _evaluate_sample_rule, _make_finding

DIGEST_HEX = "ee" * 32
ENABLED = {"ENCLOSURE_ENABLED": "true", "ENCLOSURE_MODEL": "qwen3-vl:4b-instruct",
           "ENCLOSURE_MODEL_DIGEST": "sha256:" + DIGEST_HEX, "ENCLOSURE_SAMPLE_BUDGET_SECONDS": "840"}
IDS = dict(audit_id="audit_1", sample_id="sample_1", sample_revision=2)


def rule(check="pedestrian_box_enclosure", rule_id="VISIBLE-PEDESTRIAN"):
    return Rule(rule_id=rule_id, description="Each annotation box must enclose a visible pedestrian.",
                check_name=check, origin="reviewer", confirmation_state="confirmed")


def book(*rules):
    return SimpleNamespace(rules=list(rules), rulebook_id="book_1", version=3)


def runtime(**changes):
    return load_enclosure_runtime(values={**ENABLED, **changes}, sample_max_seconds=900)


def request(**changes):
    values = dict(dataset_id="d", dataset_root="r", rulebook_id="book_1", rulebook_version=3, sample_ids=["sample_1"],
                  source_mode="snapshot")
    return BatchAuditRequest(**(values | changes))


def png():
    image = Image.new("RGB", (60, 50))
    image.putdata([((x * 5) % 256, (y * 7) % 256, 90) for y in range(50) for x in range(60)])
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def inputs(boxes, size=(60, 50)):
    text = "\n".join([f"Image size (X x Y x C) : {size[0]} x {size[1]} x 3",
                      f"Objects with ground truth : {len(boxes)} {{ }}", "Top left pixel co-ordinates : (1, 1)"])
    return {"annotation_text": text, "image_sha256": "a" * 64, "annotation_sha256": "b" * 64, "image_size": list(size),
            "objects": [{"object_id": oid, "label": "PASperson", "bbox": list(b)} for oid, b in boxes]}


def reader(boxes, fail=False):
    def read(manifest, limits, settings):
        if fail:
            raise FeedbackError("FEEDBACK_NOT_READY")
        return png(), inputs(boxes)
    return read


MANIFEST = SimpleNamespace(original_image=SimpleNamespace(sha256="a" * 64), annotation_text=SimpleNamespace(sha256="b" * 64))


class FakeOllama:
    def __init__(self, decide=None, behavior=None, digest=DIGEST_HEX):
        self.decide = decide or (lambda content: ("pass", "A pedestrian is visible in the frame."))
        self.behavior, self.digest = behavior, digest
        self.chat_bodies, self.calls, self.in_flight, self.max_in_flight = [], [], 0, 0

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, req):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.calls.append(req.url.path)
            if req.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3-vl:4b-instruct", "digest": self.digest}]})
            if req.url.path == "/api/show":
                return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
            body = json.loads(req.content)
            self.chat_bodies.append(body)
            content = json.loads(body["messages"][1]["content"])
            outcome, reason = self.decide(content)
            answer = {"case_id": content["case_id"], "rule_id": content["rule_id"], "box_number": content["box"]["box_number"],
                      "object_id": content["box"]["object_id"], "outcome": outcome, "reason": reason, "evidence_references": ["image"]}
            reply = {"model": "qwen3-vl:4b-instruct", "done": True, "done_reason": "stop", "prompt_eval_count": 900,
                     "message": {"role": "assistant", "content": json.dumps(answer)}}
            override = None if self.behavior is None else self.behavior(len(self.chat_bodies), reply, req)
            return override if isinstance(override, httpx.Response) else httpx.Response(200, json=override or reply)
        finally:
            self.in_flight -= 1


def run(boxes, fake=None, rules=None, clock=None, rt=None, other=None, read=None):
    fake = fake or FakeOllama()
    rules = rules or [rule()]
    placeholders = [_make_finding(rule=r, outcome=FindingOutcome.UNAVAILABLE, reason="model_execution_unavailable",
                                  evidence={}, object_id=None, occurrence="sample", rulebook_id="book_1", rulebook_version=3, **IDS)
                    for r in rules] + list(other or [])
    kwargs = {} if clock is None else {"clock": clock}
    result = augment_enclosure(RuleEvaluationResult(findings=placeholders, internal_check_failures=0), book(*rules), MANIFEST,
                               rt or runtime(), object(), transport=fake.transport(),
                               evidence_reader=read or reader(boxes), **IDS, **kwargs)
    return result, fake


THREE = [(1, (2, 2, 9, 9)), (1, (20, 10, 30, 25)), (4, (40, 30, 55, 48))]  # repeated object ID 1


# --- settings ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("values", [{}, {"ENCLOSURE_ENABLED": ""}, {"ENCLOSURE_ENABLED": "false"}])
def test_the_check_is_disabled_by_default(values):
    assert load_enclosure_runtime(values=values, sample_max_seconds=900) is None


def test_enabled_settings_build_an_immutable_pin_with_automatic_threads_and_prompt_hashes():
    pin = runtime().execution
    assert (pin.model, pin.model_digest, pin.num_thread, pin.max_boxes) == ("qwen3-vl:4b-instruct", "sha256:" + DIGEST_HEX, None, 12)
    assert (pin.system_prompt_sha256, pin.instruction_sha256) == (SYSTEM_PROMPT_SHA256, INSTRUCTION_SHA256)
    assert runtime(ENCLOSURE_NUM_THREAD="4").execution.num_thread == 4


@pytest.mark.parametrize("drop", ["ENCLOSURE_MODEL", "ENCLOSURE_MODEL_DIGEST", "ENCLOSURE_SAMPLE_BUDGET_SECONDS"])
def test_there_is_no_default_model_digest_or_budget(drop):
    with pytest.raises(EnclosureConfigurationError, match=drop):
        load_enclosure_runtime(values={k: v for k, v in ENABLED.items() if k != drop}, sample_max_seconds=900)


@pytest.mark.parametrize("changes", [{"ENCLOSURE_ENABLED": "maybe"}, {"ENCLOSURE_SAMPLE_BUDGET_SECONDS": "871"},
                                     {"ENCLOSURE_SAMPLE_BUDGET_SECONDS": "abc"}, {"ENCLOSURE_HOST": "http://10.0.0.5:11434"},
                                     {"ENCLOSURE_MODEL_DIGEST": "latest"}, {"ENCLOSURE_MAX_BOXES": "99"},
                                     {"ENCLOSURE_REQUEST_TIMEOUT_SECONDS": "900"}])
def test_invalid_settings_fail_safely_and_a_budget_must_fit_under_the_sample_deadline(changes):
    with pytest.raises(EnclosureConfigurationError):
        runtime(**changes)


# --- acceptance pin ----------------------------------------------------------------------------------

def test_a_book_without_the_check_is_untouched():
    original = request()
    assert prepare_enclosure_runtime(original, book(rule("annotation_guideline")), loader=lambda: runtime()) is original


def test_acceptance_freezes_the_pin_and_it_survives_serialization_and_spawn():
    accepted = prepare_enclosure_runtime(request(), book(rule()), loader=runtime)
    assert accepted.enclosure_runtime == runtime()
    again = BatchAuditRequest.model_validate_json(accepted.model_dump_json())
    assert again.enclosure_runtime == accepted.enclosure_runtime
    assert pickle.loads(pickle.dumps(accepted)).enclosure_runtime == accepted.enclosure_runtime


def test_a_disabled_check_leaves_the_request_unchanged_so_the_rule_stays_unavailable():
    original = request()
    assert prepare_enclosure_runtime(original, book(rule()), loader=lambda: None) is original


@pytest.mark.parametrize("changes", [{"feedback_policy_id": "policy_1"}, {"source_mode": "legacy"}])
def test_incompatible_policy_or_source_mode_is_rejected_clearly(changes):
    with pytest.raises(FeedbackError):
        prepare_enclosure_runtime(request(**changes), book(rule()), loader=runtime)


def test_a_pin_on_a_book_that_does_not_use_the_check_is_rejected():
    with pytest.raises(FeedbackError):
        prepare_enclosure_runtime(request(enclosure_runtime=runtime()), book(rule("annotation_guideline")))


def test_old_stored_requests_without_the_field_still_decode():
    document = json.loads(request().model_dump_json())
    document.pop("enclosure_runtime")
    assert BatchAuditRequest.model_validate(document).enclosure_runtime is None


# --- registry and deterministic evaluation -----------------------------------------------------------

def test_the_check_is_registered_as_an_explicit_parameter_free_sample_check():
    definition = get_check_definition("pedestrian_box_enclosure")
    assert definition.scope == "sample" and validate_check_parameters("pedestrian_box_enclosure", {}) is not None
    assert get_check_definition("annotation_guideline").scope == "sample"  # generic behavior unchanged
    with pytest.raises(Exception):
        validate_check_parameters("pedestrian_box_enclosure", {"tight": True})


def test_without_a_pin_the_evaluator_reports_unavailable_never_a_pass():
    rows = _evaluate_sample_rule(rule(), {"checks": {}}, {})
    assert rows == [(FindingOutcome.UNAVAILABLE, "model_execution_unavailable", None, "sample", {})]


# --- per-sample augmentation ---------------------------------------------------------------------------

def finding_of(result):
    found = [f for f in result.findings if f.check_name == "pedestrian_box_enclosure"]
    assert len(found) == 1
    return found[0], found[0].evidence.measurements["enclosure"]


def test_all_passing_boxes_pass_with_every_box_accounted_for_in_one_pending_finding():
    result, fake = run(THREE)
    finding, document = finding_of(result)
    assert finding.outcome is FindingOutcome.PASS and finding.review.resolution.value == "pending"
    assert document["boxes_intended"] == 3 and [b["box_number"] for b in document["boxes"]] == [1, 2, 3]
    assert [b["object_id"] for b in document["boxes"]] == [1, 1, 4] and [b["annotation_index"] for b in document["boxes"]] == [0, 1, 2]
    assert all(b["model_verified"] and b["view_sha256"] and b["request_sha256"] for b in document["boxes"])
    assert document["execution"]["model_digest"] == "sha256:" + DIGEST_HEX and document["protocol_version"] == "pedestrian-box-context-v1"
    assert (finding.sample_revision, finding.rulebook_id, finding.rulebook_version) == (2, "book_1", 3)
    assert fake.max_in_flight == 1 and len(fake.chat_bodies) == 3  # sequential, one request per box


def test_one_verified_failing_box_fails_the_sample_and_names_the_box():
    def decide(content):
        return ("fail", "Only background inside the frame.") if content["box"]["box_number"] == 2 else ("pass", "A person is visible.")
    finding, document = finding_of(run(THREE, FakeOllama(decide=decide))[0])
    assert (finding.outcome, finding.reason) == (FindingOutcome.FAIL, "verified_failing_box: boxes 2")
    assert [b["outcome"] for b in document["boxes"]] == ["pass", "fail", "pass"]  # no early stop


def test_unsure_and_unavailable_boxes_aggregate_deterministically():
    unsure = lambda content: ("unsure", "Hard to tell.") if content["box"]["box_number"] == 3 else ("pass", "ok")
    assert finding_of(run(THREE, FakeOllama(decide=unsure))[0])[0].outcome is FindingOutcome.UNSURE

    def timeout(count, reply, req):
        if count == 1:
            raise httpx.ReadTimeout("slow", request=req)
    finding, document = finding_of(run(THREE, FakeOllama(behavior=timeout))[0])
    assert finding.outcome is FindingOutcome.UNAVAILABLE and document["boxes"][0]["reason"] == "request_timeout"
    assert [b["model_verified"] for b in document["boxes"]] == [False, True, True]


@pytest.mark.parametrize("tamper,reason", [
    (lambda reply: {**reply, "message": {"role": "assistant", "content": json.dumps({**json.loads(reply["message"]["content"]), "object_id": 99})}}, "identity_mismatch"),
    (lambda reply: {**reply, "message": {"role": "assistant", "content": "not json"}}, "invalid_proposal"),
    (lambda reply: {**reply, "done": False}, "incomplete_response"),
    (lambda reply: {**reply, "prompt_eval_count": None}, "context_unverified"),
])
def test_unverifiable_model_output_is_unavailable_and_never_repaired(tamper, reason):
    fake = FakeOllama(behavior=lambda count, reply, req: tamper(reply))
    finding, document = finding_of(run(THREE[:1], fake)[0])
    assert finding.outcome is FindingOutcome.UNAVAILABLE and document["boxes"][0]["reason"] == reason
    assert not document["boxes"][0]["model_verified"] and document["boxes"][0]["outcome"] == "unavailable"


def test_a_changed_model_digest_makes_the_result_unavailable():
    finding, document = finding_of(run(THREE[:1], FakeOllama(digest="11" * 32))[0])
    assert finding.outcome is FindingOutcome.UNAVAILABLE and document["boxes"][0]["reason"] == "model_identity_changed"


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def test_the_sample_budget_bounds_the_work_and_unsent_boxes_stay_accounted_for():
    clock = Clock()
    def advance(count, reply, req):
        clock.value += 430
    finding, document = finding_of(run(THREE, FakeOllama(behavior=advance), clock=clock)[0])
    assert [b["outcome"] for b in document["boxes"]] == ["pass", "unavailable", "unavailable"]
    assert document["boxes"][1]["reason"] == "request_timeout"
    assert document["boxes"][2]["reason"] == "sample_budget_exhausted"
    assert finding.outcome is FindingOutcome.UNAVAILABLE
    assert len(document["boxes"]) == document["boxes_intended"] == 3


def test_budget_is_shared_between_rules_and_verified_failure_survives_exhaustion():
    clock = Clock()
    def advance(count, reply, req):
        clock.value += 430
    fake = FakeOllama(decide=lambda _: ("fail", "Background only."), behavior=advance)
    result, _ = run(THREE[:2], fake, clock=clock, rules=[rule(rule_id="R1"), rule(rule_id="R2")])
    first, second = result.findings
    assert first.outcome is FindingOutcome.FAIL
    assert second.outcome is FindingOutcome.UNAVAILABLE
    assert len(fake.chat_bodies) == 2
    assert all(b["reason"] == "sample_budget_exhausted"
               for b in second.evidence.measurements["enclosure"]["boxes"])


@pytest.mark.parametrize("preserved", [None, "enabled"])
def test_recheck_never_loads_environment_even_when_parent_was_disabled(preserved):
    original = request(enclosure_runtime=runtime() if preserved else None)
    def forbidden():
        raise AssertionError("Rechecks must not load current model settings")
    assert prepare_enclosure_runtime(original, book(rule()), loader=forbidden,
                                     preserve_runtime=True) is original


def test_client_shares_allowance_between_verification_and_chat():
    from backend.app.inference.enclosure_client import EnclosureClient
    from backend.app.services.enclosure_prompt import build_request, box_identity
    from backend.app.services.enclosure_views import render_views, ViewLimits
    clock, fake, timeouts = Clock(), FakeOllama(), []
    def handle(req):
        timeouts.append(req.extensions["timeout"]["read"])
        clock.value += 3
        return fake.handle(req)
    rt = runtime()
    view, = render_views(png(), inputs(THREE[:1])["objects"], (1, 1), ViewLimits(), max_boxes=12)
    prepared = build_request(rt.execution, rule().model_dump(mode="json"), "a" * 64, view)
    client = EnclosureClient(rt.execution, clock=clock, transport=httpx.MockTransport(handle))
    try:
        call = client.chat(prepared, box_identity("a" * 64, rule().rule_id, view), 10)
    finally:
        client.close()
    assert timeouts == [10, 7, 4, 1]
    assert call.failure == "request_timeout" and not call.model_verified and call.response is None


def test_expired_processing_allowance_sends_no_model_requests():
    from datetime import UTC, datetime
    fake = FakeOllama()
    result = augment_enclosure(RuleEvaluationResult(findings=[], internal_check_failures=0), book(rule()),
        MANIFEST, runtime(), object(), transport=fake.transport(), evidence_reader=reader(THREE),
        processing_deadline=datetime(2000, 1, 1, tzinfo=UTC), **IDS)
    assert not fake.calls
    assert all(b["reason"] == "sample_budget_exhausted"
               for b in finding_of(result)[1]["boxes"])


def test_checkpoint_loss_propagates_before_any_model_call():
    from backend.app.storage.workflow_repository import JobClaimLostError
    fake = FakeOllama()
    def lost():
        raise JobClaimLostError("replaced")
    with pytest.raises(JobClaimLostError):
        augment_enclosure(RuleEvaluationResult(findings=[], internal_check_failures=0), book(rule()),
            MANIFEST, runtime(), object(), transport=fake.transport(), evidence_reader=reader(THREE),
            checkpoint=lost, **IDS)
    assert not fake.calls


@pytest.mark.parametrize("boxes,code", [
    ([], "no_annotation_boxes"),
    ([(1, (2, 2, 9, 9))] * 13, "too_many_annotation_boxes"),
    ([(1, (0, 2, 9, 9))], "box_outside_source_or_inverted"),
    ([(1, (2, 2, 99, 9))], "box_outside_source_or_inverted"),
])
def test_zero_too_many_or_invalid_boxes_are_explicit_unavailable_never_a_pass(boxes, code):
    result, fake = run(boxes)
    finding, document = finding_of(result)
    assert (finding.outcome, finding.reason) == (FindingOutcome.UNAVAILABLE, code)
    assert document["failure"] == code and fake.chat_bodies == []  # nothing was sent


def test_unreadable_evidence_is_unavailable_and_the_pinned_hashes_are_still_recorded():
    finding, document = finding_of(run(THREE, read=reader(THREE, fail=True))[0])
    assert (finding.outcome, finding.reason) == (FindingOutcome.UNAVAILABLE, "evidence_unavailable")
    assert document["image_sha256"] == "a" * 64


def test_other_findings_are_kept_and_unrelated_rules_are_untouched():
    other = _make_finding(rule=rule("annotation_guideline", "GUIDE"), outcome=FindingOutcome.UNAVAILABLE, reason="model_execution_unavailable",
                          evidence={}, object_id=None, occurrence="sample", rulebook_id="book_1", rulebook_version=3, **IDS)
    result, _ = run(THREE, other=[other])
    assert other in result.findings and len(result.findings) == 2


def test_no_pin_or_no_enclosure_rule_changes_nothing():
    evaluation = RuleEvaluationResult(findings=[], internal_check_failures=0)
    assert augment_enclosure(evaluation, book(rule()), MANIFEST, None, object(), **IDS) is evaluation
    assert augment_enclosure(evaluation, book(rule("annotation_guideline")), MANIFEST, runtime(), object(), **IDS) is evaluation


def test_requests_carry_no_source_coordinates_labels_or_other_boxes_and_hashes_match_the_bytes_sent():
    from hashlib import sha256
    result, fake = run(THREE)
    _, document = finding_of(result)
    for body in fake.chat_bodies:
        text = body["messages"][1]["content"]
        for forbidden in ("bbox", "source_window", "[2, 2, 9, 9]", "annotation_text", "expected", "decision"):
            assert forbidden not in text
    assert {b["request_sha256"] for b in document["boxes"]} == {sha256(json.dumps(b, ensure_ascii=False, allow_nan=False).encode()).hexdigest() for b in fake.chat_bodies}


def test_two_enclosure_rules_each_get_their_own_finding():
    result, _ = run(THREE[:1], rules=[rule(rule_id="R1"), rule(rule_id="R2")])
    assert sorted(f.rule_id for f in result.findings) == ["R1", "R2"] and len({f.finding_id for f in result.findings}) == 2


# --- worker wiring -------------------------------------------------------------------------------------

class Pipe:
    def __init__(self):
        self.chunks, self.closed = [], False

    def send_bytes(self, data):
        self.chunks.append(data)

    def close(self):
        self.closed = True

    def message(self):
        return pickle.loads(b"".join(c for c in self.chunks if c))


def child(monkeypatch, *, manifest=None, settings=None, enclosure=None):
    pipe = Pipe()
    evaluation = RuleEvaluationResult(findings=[], internal_check_failures=0)
    monkeypatch.setattr(sample_worker, "materialize_sample", lambda *a, **k: None)
    sample_worker.run_sample_child(pipe, lambda *a: {"status": "complete"}, lambda *a, **k: evaluation, book(rule()), "root",
                                   "sample_1", None, 8, "audit_1", 2, snapshot_manifest=manifest, asset_settings=settings,
                                   enclosure_runtime=enclosure)
    return pipe.message(), evaluation


def test_the_spawned_child_requires_immutable_evidence_for_the_check(monkeypatch):
    message, _ = child(monkeypatch, enclosure=runtime())
    assert message[0] == "fatal" and "immutable snapshot" in message[2]


def test_the_spawned_child_runs_the_enclosure_step_after_evaluation(monkeypatch):
    calls = []
    marker = RuleEvaluationResult(findings=[], internal_check_failures=7)

    def fake_augment(evaluation, rulebook, manifest, rt, settings, **ids):
        calls.append((manifest, rt, ids))
        return marker

    monkeypatch.setattr("backend.app.services.enclosure.augment_enclosure", fake_augment)
    message, _ = child(monkeypatch, manifest=SimpleNamespace(), settings=object(), enclosure=runtime())
    assert message[0] == "success" and message[3] == 7 and calls[0][2] == {"audit_id": "audit_1", "sample_id": "sample_1", "sample_revision": 2, "processing_deadline": None}


def test_without_a_pin_the_child_is_unchanged(monkeypatch):
    message, _ = child(monkeypatch)
    assert message[0] == "success" and message[3] == 0
