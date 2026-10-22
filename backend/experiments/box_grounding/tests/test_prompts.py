"""Request payloads: faithful control, neutral candidate, no truth leakage, no truncation."""

from base64 import b64decode
from hashlib import sha256
import inspect
import json

import pytest

from backend.app.models.feedback import Prediction
from backend.experiments.box_grounding import prompts
from backend.experiments.box_grounding.crops import render_box_crops
from backend.experiments.box_grounding.prompts import (
    BoxPrediction, ContextNotRepresentable, crop_identity, crop_request, load_prompt_texts, raw_identity,
    raw_request, rule_document,
)
from backend.experiments.box_grounding.scope import FORBIDDEN_KEYS
from backend.experiments.box_grounding.settings import ExecutionSettings
from backend.experiments.box_grounding.views import ViewLimits

from box_helpers import DIGEST, GUIDELINE, PACKET

NUMBERED = json.loads((PACKET / "reference_results/numbered_overview.json").read_bytes())


def execution(**changes):
    return ExecutionSettings(model_digest=DIGEST, **changes)


@pytest.fixture
def parts(corpus):
    scope = corpus.scope()
    case = next(c for c in scope.cases if c.case_name == "case-02")
    crops = render_box_crops(case.image_bytes, case.objects, (1, 1), ViewLimits())
    return scope, case, crops, load_prompt_texts(corpus.packet)


def user_content(request):
    return json.loads(request.payload["messages"][1]["content"])


def test_the_system_prompt_is_the_historical_text_exactly():
    texts = load_prompt_texts()
    assert texts.system_sha256 == NUMBERED["provenance"]["system_prompt_sha256"]
    assert texts.system.startswith("Judge ONE confirmed annotation guideline")
    assert sha256(texts.per_box_instruction.encode()).hexdigest() == texts.per_box_instruction_sha256


def test_the_raw_control_mirrors_the_production_request_shape(parts):
    scope, case, _, texts = parts
    request = raw_request(scope, case, texts, execution())
    payload = request.payload
    assert list(payload) == ["model", "stream", "keep_alive", "messages", "format", "options"]
    assert payload["stream"] is False and payload["keep_alive"] == 300
    assert payload["options"] == {"num_gpu": 0, "temperature": 0, "num_ctx": 16384, "num_predict": 2048}
    assert payload["format"] == Prediction.model_json_schema()
    system, user = payload["messages"]
    assert system == {"role": "system", "content": texts.system}
    assert [b64decode(i) for i in user["images"]] == [case.image_bytes]  # the original bytes, untouched
    content = user_content(request)
    assert content["examples"] == [] and content["guideline"] == rule_document(scope)
    assert list(content["target"]["inputs"]) == list(prompts.INPUT_ORDER)
    assert content["target"]["inputs"]["objects"] == case.objects  # every box, repeats included
    assert content["case_id"] == case.case_id and content["rule_id"] == scope.rule_id


def test_the_guideline_is_a_production_rule_with_the_exact_rule_text(parts):
    scope, *_ = parts
    rule = rule_document(scope)
    assert rule["description"] == GUIDELINE and rule["origin"] == "reviewer"
    assert rule["confirmation_state"] == "confirmed" and rule["parameters"] == {}


def test_thread_count_is_sent_only_when_pinned(parts):
    scope, case, _, texts = parts
    assert "num_thread" not in raw_request(scope, case, texts, execution()).payload["options"]
    assert raw_request(scope, case, texts, execution(num_thread=4)).payload["options"]["num_thread"] == 4


def test_each_crop_request_carries_one_raw_crop_and_only_that_boxes_facts(parts):
    scope, case, crops, texts = parts
    requests = [crop_request(scope, case, crop, texts, execution()) for crop in crops]
    for crop, request in zip(crops, requests):
        user = request.payload["messages"][1]
        assert [b64decode(i) for i in user["images"]] == [crop.png]  # exactly the crop, nothing else
        box = user_content(request)["box"]
        assert box["box_number"] == crop.box_number and box["object_id"] == crop.object_id
        assert box["bbox"] == list(crop.bbox) and box["crop_size"] == [crop.width, crop.height]
        assert request.payload["format"] == BoxPrediction.model_json_schema()
    text = request.payload["messages"][1]["content"]
    assert "annotation_text" not in text and "objects" not in text  # no other boxes, no full annotation file
    assert len({r.sha256 for r in requests}) == len(requests)


def test_the_candidate_wording_is_neutral_and_versioned(parts):
    scope, case, crops, texts = parts
    task = user_content(crop_request(scope, case, crops[0], texts, execution()))["task"]
    assert task["variant"] == "box-crop-v1" and task["instruction_version"] == "per-box-instruction-v1"
    assert task["instruction"] == texts.per_box_instruction
    lowered = texts.per_box_instruction.lower()
    for word in ("alter", "perturb", "modif", "synthetic", "deliberate", "controlled", "tamper", "edited", "shifted"):
        assert word not in lowered  # never announce that a box was deliberately changed


def test_the_system_prompt_is_identical_in_both_variants(parts):
    scope, case, crops, texts = parts
    raw = raw_request(scope, case, texts, execution()).payload["messages"][0]
    crop = crop_request(scope, case, crops[0], texts, execution()).payload["messages"][0]
    assert raw == crop == {"role": "system", "content": texts.system}


def test_builders_cannot_receive_labels_and_payloads_contain_no_label_fields(parts):
    scope, case, crops, texts = parts
    for builder in (raw_request, crop_request):
        names = set(inspect.signature(builder).parameters)
        assert not names & {"expected", "label", "labels", "decision", "adjudication", "examples"}
    for request in [raw_request(scope, case, texts, execution())] + [
            crop_request(scope, case, crop, texts, execution()) for crop in crops]:
        serialized = request.body.decode()
        for key in FORBIDDEN_KEYS:
            assert f'"{key}"' not in serialized


def test_request_hash_describes_the_exact_bytes_sent(parts):
    scope, case, _, texts = parts
    request = raw_request(scope, case, texts, execution())
    assert request.sha256 == sha256(request.body).hexdigest()
    assert json.loads(request.body) == request.payload
    assert request.image_count == 1


def test_identities_pin_case_rule_box_and_object(parts):
    scope, case, crops, _ = parts
    assert raw_identity(scope, case) == {"case_id": case.case_id, "rule_id": scope.rule_id}
    assert crop_identity(scope, case, crops[1]) == {
        "case_id": case.case_id, "rule_id": scope.rule_id, "box_number": 2, "object_id": 1}


@pytest.mark.parametrize("changes", [{"num_ctx": 1024}, {"max_request_bytes": 1024}])
def test_unrepresentable_context_is_rejected_never_truncated(parts, changes):
    scope, case, crops, texts = parts
    with pytest.raises(ContextNotRepresentable) as caught:
        raw_request(scope, case, texts, execution(**changes))
    assert caught.value.code == "context_limit"
    with pytest.raises(ContextNotRepresentable):
        crop_request(scope, case, crops[0], texts, execution(**changes))


@pytest.mark.parametrize("change", [{"box_number": 0}, {"box_number": 33}, {"extra": 1}, {"object_id": "1"},
                                    {"outcome": "maybe"}, {"reason": ""}, {"evidence_references": ["a", "b", "c", "d"]}])
def test_the_box_schema_is_strict(change):
    good = dict(case_id="0" * 64, rule_id="R", box_number=1, object_id=1, outcome="pass", reason="ok", evidence_references=[])
    assert BoxPrediction.model_validate(good)
    with pytest.raises(ValueError):
        BoxPrediction.model_validate({**good, **change})


def test_schema_versions_and_variant_identities_are_explicit():
    assert prompts.PREPROCESSING == {"raw-image-v1": "original-png-no-transform-v1",
                                     "box-crop-v1": "box-crop-png-v1", "box-crop-v2": "box-crop-png-v1"}
    assert prompts.BOX_SCHEMA_VERSION == "box-prediction-v1"
    assert prompts.DEFAULT_ARMS == ("raw-image-v1", "box-crop-v1")
    assert prompts.VARIANTS == ("raw-image-v1", "box-crop-v1", "box-crop-v2")


def test_v2_changes_only_variant_identity_and_source_geometry(parts):
    scope, case, crops, texts = parts
    crop = crops[0]
    original = crop_request(scope, case, crop, texts, execution())
    revised = crop_request(scope, case, crop, texts, execution(), variant="box-crop-v2")
    before, after = user_content(original), user_content(revised)
    before["task"]["variant"] = "box-crop-v2"
    for key in ("bbox", "bbox_convention", "source_image_size"):
        del before["box"][key]
        assert key not in after["box"]
    assert before == after
    left, right = json.loads(original.body), json.loads(revised.body)
    left["messages"][1]["content"] = right["messages"][1]["content"]
    assert left == right  # identical pixels, options, system prompt and schema
    assert b64decode(right["messages"][1]["images"][0]) == crop.png
    assert revised.sha256 == sha256(revised.body).hexdigest()


def test_unknown_crop_version_is_rejected(parts):
    scope, case, crops, texts = parts
    with pytest.raises(ValueError, match="Unsupported crop variant"):
        crop_request(scope, case, crops[0], texts, execution(), variant="box-crop-typo")
