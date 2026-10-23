"""Every safeguard of the production guideline client: each failure is an explicit, unretried unavailable."""

import json

import httpx
import pytest

from backend.app.models.feedback import Prediction
from backend.experiments.box_grounding.client import OllamaClient, unique_object
from backend.experiments.box_grounding.crops import render_box_crops
from backend.experiments.box_grounding.prompts import (
    BoxPrediction, crop_identity, crop_request, load_prompt_texts, raw_identity, raw_request,
)
from backend.experiments.box_grounding.settings import ExecutionSettings
from backend.experiments.box_grounding.views import ViewLimits

from box_helpers import DIGEST, FakeOllama

SAFE_RESPONSES = {"model": "qwen3-vl:4b-instruct", "done": True, "done_reason": "stop", "prompt_eval_count": 600}


@pytest.fixture
def setup(corpus):
    scope = corpus.scope()
    case = next(c for c in scope.cases if c.case_name == "case-02")
    texts = load_prompt_texts(corpus.packet)

    def build(fake, **limits):
        execution = ExecutionSettings(model_digest=DIGEST, **limits)
        client = OllamaClient(execution, transport=fake.transport())
        crop = render_box_crops(case.image_bytes, case.objects, (1, 1), ViewLimits())[0]
        return client, execution, {
            "raw": (raw_request(scope, case, texts, execution), Prediction, raw_identity(scope, case)),
            "crop": (crop_request(scope, case, crop, texts, execution), BoxPrediction, crop_identity(scope, case, crop)),
        }

    return build


def call(setup, fake, kind="raw", **limits):
    client, _, requests = setup(fake, **limits)
    with client:
        return client.chat(*requests[kind])


def test_a_good_response_is_verified_and_records_server_metrics(setup):
    for kind in ("raw", "crop"):
        fake = FakeOllama()
        result = call(setup, fake, kind)
        assert result.model_verified and result.reason is None and result.prediction.outcome == "pass"
        assert result.metrics["prompt_eval_count"] == 600 and result.chat_attempted
        assert result.chat_ms is not None and result.duration_ms >= result.chat_ms
        assert fake.calls == [("GET", "/api/tags"), ("POST", "/api/show"), ("POST", "/api/chat"),
                              ("GET", "/api/tags"), ("POST", "/api/show")]  # verified before and after


def test_the_client_is_local_unproxied_and_does_not_follow_redirects(setup):
    client, _, _ = setup(FakeOllama())
    assert client.client.follow_redirects is False and client.client._trust_env is False
    assert str(client.client.base_url).startswith("http://127.0.0.1")


@pytest.mark.parametrize("fake,reason", [
    (FakeOllama(digest="sha256:" + "11" * 32), "model_identity_changed"),
    (FakeOllama(model="another:tag"), "identity_unavailable"),
    (FakeOllama(capabilities=["completion"]), "local_vision_unverified"),
    (FakeOllama(show_extra={"remote_model": "cloud"}), "local_vision_unverified"),
])
def test_an_unverified_local_model_is_unavailable_before_any_chat(setup, fake, reason):
    result = call(setup, fake)
    assert (result.prediction, result.reason, result.model_verified, result.chat_attempted) == (None, reason, False, False)
    assert fake.chat_count == 0


def test_a_model_that_changes_during_the_call_is_unavailable_and_unverified(setup):
    fake = FakeOllama(digest_after_chat="sha256:" + "22" * 32)
    result = call(setup, fake)
    assert result.reason == "model_identity_changed" and result.prediction is None and not result.model_verified
    assert result.chat_attempted and result.content is not None  # the answer is retained for the record


@pytest.mark.parametrize("behavior,reason", [
    (lambda c, r, req: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=req)), "request_timeout"),
    (lambda c, r, req: (_ for _ in ()).throw(httpx.ConnectError("down", request=req)), "connection_failed"),
    (lambda c, r, req: httpx.Response(500, json={"error": "boom"}), "http_error"),
    (lambda c, r, req: httpx.Response(200, content=b"not json"), "invalid_response"),
    (lambda c, r, req: httpx.Response(200, content=b'["a list"]'), "invalid_response"),
    (lambda c, r, req: {"error": "model failed"}, "invalid_response"),
    (lambda c, r, req: httpx.Response(200, content=b'{"done": true, "done": true}'), "invalid_response"),
    (lambda c, r, req: httpx.Response(200, content=b'{"x": "' + b"a" * 70_000 + b'"}'), "response_limit"),
])
def test_transport_and_envelope_failures_become_explicit_reasons_without_a_retry(setup, behavior, reason):
    fake = FakeOllama(behavior=behavior)
    result = call(setup, fake)
    assert (result.prediction, result.reason, result.model_verified) == (None, reason, False)
    assert fake.chat_count == 1  # never retried


@pytest.mark.parametrize("patch,reason", [
    ({"model": "someone-else:latest"}, "model_identity_changed"),
    ({"prompt_eval_count": None}, "context_unverified"),
    ({"prompt_eval_count": 0}, "context_unverified"),
    ({"prompt_eval_count": 16384 - 2048}, "context_unverified"),
    ({"prompt_eval_count": "600"}, "context_unverified"),
    ({"done": False}, "incomplete_response"),
    ({"done_reason": "length"}, "incomplete_response"),
    ({"message": {"role": "assistant"}}, "invalid_response"),
    ({"message": "text"}, "invalid_response"),
])
def test_incomplete_oversized_or_misattributed_responses_are_unavailable(setup, patch, reason):
    def behavior(count, reply, request):
        return {**reply, **patch}
    result = call(setup, FakeOllama(behavior=behavior))
    assert (result.prediction, result.reason, result.model_verified) == (None, reason, False)


def reply_with(content):
    def behavior(count, reply, request):
        return {**reply, "message": {"role": "assistant", "content": content}}
    return behavior


GOOD = {"case_id": "", "rule_id": "VISIBLE-PEDESTRIAN", "outcome": "pass", "reason": "ok", "evidence_references": []}


@pytest.mark.parametrize("content", [
    "not json at all", "", "[]", '{"outcome": "pass", "outcome": "pass"}',
    json.dumps({**GOOD, "case_id": "0" * 64, "outcome": "maybe"}),
    json.dumps({**GOOD, "case_id": "0" * 64, "surprise": 1}),
    json.dumps({**GOOD, "case_id": "0" * 64, "reason": ""}),
    json.dumps({"outcome": "pass"}),
])
def test_malformed_unstructured_or_duplicate_field_content_is_an_invalid_proposal(setup, content):
    result = call(setup, FakeOllama(behavior=reply_with(content)))
    assert (result.prediction, result.reason, result.model_verified) == (None, "invalid_proposal", False)


@pytest.mark.parametrize("kind,change", [
    ("raw", {"case_id": "f" * 64}), ("raw", {"rule_id": "OTHER-RULE"}),
    ("crop", {"case_id": "f" * 64}), ("crop", {"rule_id": "OTHER"}),
    ("crop", {"box_number": 2}), ("crop", {"object_id": 99}),
])
@pytest.mark.parametrize("identity_schema", ["echo-v1", "bound-v1"])
def test_an_incorrect_returned_identity_is_never_repaired(setup, kind, change, identity_schema):
    def behavior(count, reply, request):
        prediction = json.loads(reply["message"]["content"])
        return reply_with(json.dumps({**prediction, **change}))(count, reply, request)
    result = call(setup, FakeOllama(behavior=behavior), kind, identity_schema=identity_schema)
    assert (result.prediction, result.reason, result.model_verified) == (None, "identity_mismatch", False)


def test_a_request_over_the_byte_limit_is_refused_before_it_is_sent(setup):
    fake = FakeOllama()
    client, execution, requests = setup(fake)
    tight = execution.model_copy(update={"max_request_bytes": 1024})
    client.execution = tight
    result = client.chat(*requests["raw"])
    assert result.reason == "context_limit" and fake.chat_count == 0 and not result.model_verified
    client.close()


def test_a_failure_is_never_an_empty_pass(setup):
    for behavior in (reply_with("{}"), lambda c, r, req: httpx.Response(200, content=b"{}")):
        result = call(setup, FakeOllama(behavior=behavior))
        assert result.prediction is None and result.reason is not None and not result.model_verified


def test_server_discovery_helpers(setup):
    fake = FakeOllama()
    client, execution, _ = setup(fake)
    assert client.installed_digest() == execution.model_digest == DIGEST
    assert client.server_version() == "0.0.0-test"
    client.close()


def test_duplicate_fields_are_rejected_by_the_object_hook():
    with pytest.raises(ValueError):
        json.loads('{"a": 1, "a": 2}', object_pairs_hook=unique_object)
    assert json.loads('{"a": 1}', object_pairs_hook=unique_object) == {"a": 1}
