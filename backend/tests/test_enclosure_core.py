"""Pure core of the pedestrian-box enclosure check: views, pin, aggregation and requests."""

from base64 import b64decode
from hashlib import sha256
from io import BytesIO
from itertools import product
import json

from PIL import Image
import pytest

from backend.app.models.enclosure import (
    BoxResponse, BoxVerdict, EnclosureExecutionPin, EnclosureRuntime, aggregate_sample, bound_response_schema,
)
from backend.app.services import enclosure_prompt as prompts
from backend.app.services.enclosure_views import EnclosureViewError, ViewLimits, render_views

RULE = {"rule_id": "VISIBLE-PEDESTRIAN", "description": "Each annotation box must enclose a visible pedestrian.",
        "check_name": "pedestrian_box_enclosure", "parameters": {}, "origin": "reviewer",
        "source_sentence": None, "confirmation_state": "confirmed"}
CASE_ID = "ab" * 32


def picture(width=40, height=30):
    image = Image.new("RGB", (width, height))
    image.putdata([((x * 7) % 256, (y * 11) % 256, (x + y) % 256) for y in range(height) for x in range(width)])
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue(), image


def box(bbox, object_id=1, label="PASperson"):
    return {"object_id": object_id, "label": label, "bbox": list(bbox)}


def pin(**changes):
    values = dict(host="http://127.0.0.1:11434", model="qwen3-vl:4b-instruct", model_digest="sha256:" + "ee" * 32,
                  system_prompt_sha256=prompts.SYSTEM_PROMPT_SHA256, instruction_sha256=prompts.INSTRUCTION_SHA256,
                  num_ctx=16384, num_predict=2048, request_timeout_seconds=600, sample_budget_seconds=840,
                  max_boxes=12, max_request_bytes=64 * 1024**2, max_response_bytes=65536, keep_alive_seconds=300)
    return EnclosureExecutionPin(**(values | changes))


def interior(view, source):
    """The target pixels as they appear in the attached view, and as they are in the source."""
    x0, y0, x1, y1 = view.target.pil_box
    left, top, right, bottom = view.target_window
    with Image.open(BytesIO(view.png)) as attached:
        return attached.convert("RGB").crop((left, top, right, bottom)), source.crop((x0, y0, x1, y1))


# --- target pixels, geometry and the frame -------------------------------------------------------

@pytest.mark.parametrize("bbox", [(1, 1, 40, 30), (1, 1, 1, 1), (40, 30, 40, 30), (1, 5, 3, 9), (35, 20, 40, 30), (10, 10, 20, 12)])
def test_target_pixels_are_preserved_exactly_including_edges_and_single_pixels(bbox):
    data, source = picture()
    [view] = render_views(data, [box(bbox)], (1, 1), ViewLimits())
    attached, original = interior(view, source)
    assert attached.tobytes() == original.tobytes()
    assert view.target.pil_box == (bbox[0] - 1, bbox[1] - 1, bbox[2], bbox[3])


def test_the_red_frame_sits_entirely_outside_the_target_and_the_margin_is_neutral():
    data, source = picture()
    [view] = render_views(data, [box((10, 10, 20, 20))], (1, 1), ViewLimits())
    with Image.open(BytesIO(view.png)) as opened:
        attached = opened.convert("RGB")
    left, top, right, bottom = view.target_window
    assert attached.getpixel((left - 1, top + 3)) == (255, 0, 0) and attached.getpixel((left - 2, top + 3)) == (255, 0, 0)
    assert attached.getpixel((right, bottom - 1)) == (255, 0, 0)
    assert attached.getpixel((0, 0)) in {(255, 0, 0), (128, 128, 128)}
    assert attached.getpixel((attached.width - 1, attached.height // 2)) in {(255, 0, 0), (128, 128, 128)}


@pytest.mark.parametrize("bbox,window", [
    ((20, 15, 21, 16), (0, 0, 52, 47)),   # one-pixel target in a 40 x 30 image: 32 px minimum, clipped
    ((1, 1, 4, 4), (0, 0, 36, 30)),       # clipped at the top-left corner and the image height
    ((37, 27, 40, 30), (4, 0, 40, 30)),   # clipped at the bottom-right corner
])
def test_context_is_half_the_box_with_a_32_pixel_minimum_and_clips_only_at_the_source(bbox, window):
    data, _ = picture(60, 50) if bbox == (20, 15, 21, 16) else picture()
    [view] = render_views(data, [box(bbox)], (1, 1), ViewLimits())
    if bbox == (20, 15, 21, 16):
        assert view.source_window == (0, 0, 53, 48)  # 19 - 32 clips to 0; 21 + 32 = 53; 15 + 32 + 1 = 48
    else:
        assert view.source_window == window


def test_large_targets_get_half_their_size_per_side_rounded_up():
    data, _ = picture(400, 400)
    [view] = render_views(data, [box((101, 101, 181, 171))], (1, 1), ViewLimits())  # 81 x 71 target
    assert view.source_window == (100 - 41, 100 - 36, 181 + 41, 171 + 36)


def test_views_are_deterministic_and_hashed_from_their_bytes():
    data, _ = picture()
    first, second = render_views(data, [box((3, 3, 9, 9))], (1, 1), ViewLimits())[0], render_views(data, [box((3, 3, 9, 9))], (1, 1), ViewLimits())[0]
    assert first.png == second.png and first.png_sha256 == sha256(first.png).hexdigest()


def test_duplicate_object_ids_and_annotation_order_are_preserved():
    data, _ = picture()
    objects = [box((30, 20, 35, 25), 4), box((2, 2, 6, 6), 4), box((10, 10, 12, 12), 2)]
    views = render_views(data, objects, (1, 1), ViewLimits())
    assert [v.target.object_id for v in views] == [4, 4, 2]
    assert [v.target.annotation_index for v in views] == [0, 1, 2] and [v.target.box_number for v in views] == [1, 2, 3]
    assert [v.target.bbox for v in views] == [(30, 20, 35, 25), (2, 2, 6, 6), (10, 10, 12, 12)]
    assert len({v.png_sha256 for v in views}) == 3


def test_rendering_never_changes_the_source_or_the_annotations():
    data, _ = picture()
    objects = [box((3, 3, 9, 9))]
    before = (data, json.dumps(objects))
    render_views(data, objects, (1, 1), ViewLimits())
    assert (data, json.dumps(objects)) == before


@pytest.mark.parametrize("bbox", [[0, 1, 4, 3], [1, 1, 41, 3], [5, 1, 4, 3], [1, 1, 4, 31], [1.0, 1, 4, 3], [True, 1, 4, 3], [1, 1, 4], None])
def test_invalid_geometry_is_rejected_never_repaired_dropped_or_clipped(bbox):
    data, _ = picture()
    with pytest.raises(EnclosureViewError):
        render_views(data, [box((1, 1, 2, 2)), box((1, 1, 2, 2)) | {"bbox": bbox}], (1, 1), ViewLimits())


@pytest.mark.parametrize("obj", [{"bbox": [1, 1, 2, 2]}, box((1, 1, 2, 2), object_id=True), box((1, 1, 2, 2), label=" ")])
def test_missing_or_malformed_object_fields_are_rejected(obj):
    with pytest.raises(EnclosureViewError, match="invalid_object_fields"):
        render_views(picture()[0], [obj], (1, 1), ViewLimits())


@pytest.mark.parametrize("objects,origin,code", [
    ([], (1, 1), "no_annotation_boxes"),
    ([box((1, 1, 2, 2))] * 13, (1, 1), "too_many_annotation_boxes"),
    ([box((1, 1, 2, 2))], (0, 0), "unsupported_coordinate_origin"),
])
def test_zero_too_many_or_zero_based_annotations_are_explicit_errors(objects, origin, code):
    with pytest.raises(EnclosureViewError, match=code):
        render_views(picture()[0], objects, origin, ViewLimits(), max_boxes=12)


@pytest.mark.parametrize("data,limits,code", [
    (b"not an image", ViewLimits(), "source_image_unreadable"),
    (picture()[0], ViewLimits(max_image_bytes=10), "source_byte_limit"),
    (picture()[0], ViewLimits(max_pixels=100), "source_pixel_limit"),
])
def test_unreadable_or_oversized_sources_fail_explicitly(data, limits, code):
    with pytest.raises(EnclosureViewError, match=code):
        render_views(data, [box((1, 1, 2, 2))], (1, 1), limits)


# --- aggregation ---------------------------------------------------------------------------------

def oracle(verdicts):
    if not verdicts:
        return "unavailable"
    outcomes = {v.outcome for v in verdicts}
    return "fail" if "fail" in outcomes else "unavailable" if "unavailable" in outcomes else "unsure" if "unsure" in outcomes else "pass"


@pytest.mark.parametrize("size", [1, 2, 3])
def test_aggregation_is_exhaustive_and_deterministic(size):
    options = [BoxVerdict(outcome=o, model_verified=True) for o in ("pass", "fail", "unsure", "unavailable")]
    options.append(BoxVerdict(outcome="unavailable", model_verified=False))
    for combo in product(options, repeat=size):
        assert aggregate_sample(list(combo))[0] == oracle(list(combo))


def test_no_boxes_is_unavailable_with_an_explicit_reason_never_a_pass():
    assert aggregate_sample([]) == ("unavailable", "no_annotation_boxes")


def test_an_unverified_result_cannot_carry_a_decision():
    with pytest.raises(ValueError):
        BoxVerdict(outcome="fail", model_verified=False)
    with pytest.raises(ValueError):
        BoxVerdict(outcome="pass", model_verified=False)


# --- the immutable pin ----------------------------------------------------------------------------

def test_the_pin_round_trips_through_json_and_is_immutable():
    runtime = EnclosureRuntime(execution=pin(num_thread=4))
    again = EnclosureRuntime.model_validate_json(runtime.model_dump_json())
    assert again == runtime and pickle_round_trip(runtime) == runtime
    with pytest.raises(Exception):
        runtime.execution.num_ctx = 2048  # frozen


def pickle_round_trip(value):
    import pickle
    return pickle.loads(pickle.dumps(value))


@pytest.mark.parametrize("changes", [
    {"host": "http://192.168.1.4:11434"}, {"host": "http://user:pw@127.0.0.1:11434"}, {"model_digest": "sha256:short"},
    {"num_ctx": 512}, {"num_predict": 4096}, {"max_boxes": 33}, {"num_thread": 0},
    {"request_timeout_seconds": 900, "sample_budget_seconds": 600}, {"temperature": 1}, {"unknown": 1},
])
def test_the_pin_rejects_unsafe_or_inconsistent_settings(changes):
    with pytest.raises(ValueError):
        pin(**changes)


def test_automatic_thread_selection_is_the_recorded_default():
    assert pin().num_thread is None and "num_thread" not in prompts.build_request(
        pin(), RULE, CASE_ID, render_views(picture()[0], [box((3, 3, 9, 9))], (1, 1), ViewLimits())[0]).payload["options"]


def test_a_pin_frozen_against_different_prompt_text_fails_closed():
    with pytest.raises(prompts.PromptMismatch):
        prompts.verify_pin_prompts(pin(instruction_sha256="0" * 64))
    prompts.verify_pin_prompts(pin())


# --- requests ---------------------------------------------------------------------------------------

def request_for(bbox=(10, 10, 20, 20), **changes):
    data, _ = picture()
    view = render_views(data, [box(bbox, 7, "PASpersonWalking")], (1, 1), ViewLimits())[0]
    return prompts.build_request(pin(**changes), RULE, CASE_ID, view), view


def test_the_system_prompt_is_the_historical_text_and_the_request_has_the_expected_shape():
    from backend.app.services.feedback_prompt import SYSTEM
    request, view = request_for(num_thread=3)
    assert prompts.SYSTEM_PROMPT == SYSTEM
    payload = request.payload
    assert list(payload) == ["model", "stream", "keep_alive", "messages", "format", "options"]
    assert payload["options"] == {"num_gpu": 0, "temperature": 0, "num_ctx": 16384, "num_predict": 2048, "num_thread": 3}
    assert payload["messages"][0] == {"role": "system", "content": SYSTEM}
    assert [b64decode(i) for i in payload["messages"][1]["images"]] == [view.png]
    assert request.sha256 == sha256(request.body).hexdigest() and json.loads(request.body) == payload


def test_no_source_coordinates_other_boxes_or_label_decisions_reach_the_model():
    request, view = request_for((10, 10, 20, 20))
    content = json.loads(request.payload["messages"][1]["content"])
    text = request.payload["messages"][1]["content"]
    assert content["box"] == {"box_number": 1, "object_id": 7, "label": "PASpersonWalking"}
    assert content["target"] == {"image_attachment": 1, "marker": prompts.MARKER}
    for forbidden in ("bbox", "coordinates", "source_window", "target_window", "expected", "decision", "reviewer_id", "[10, 10, 20, 20]"):
        assert forbidden not in text
    assert content["task"]["instruction"] == prompts.INSTRUCTION and "partly visible" in prompts.INSTRUCTION
    for word in ("tight", "walking motion") :
        assert word in prompts.INSTRUCTION  # the instruction explicitly disclaims these requirements


def test_the_response_schema_binds_only_known_identities_and_is_fresh_per_call():
    request, view = request_for()
    props = request.payload["format"]["properties"]
    expected = {"case_id": CASE_ID, "rule_id": RULE["rule_id"], "box_number": 1, "object_id": 7}
    assert {k: props[k]["const"] for k in expected} == expected
    assert all("const" not in props[k] and "enum" not in props[k] or k == "outcome" for k in ("reason", "evidence_references"))
    assert "const" not in props["outcome"]  # the decision is never constrained to an expected answer
    first, second = bound_response_schema(expected), bound_response_schema(expected)
    first["properties"]["box_number"]["const"] = 99
    assert second["properties"]["box_number"]["const"] == 1
    assert BoxResponse.model_json_schema()["properties"]["box_number"].get("const") is None  # the class schema is not mutated


@pytest.mark.parametrize("changes", [{"num_ctx": 1024, "num_predict": 128}, {"max_request_bytes": 1024}])
def test_unrepresentable_requests_are_rejected_not_truncated(changes):
    with pytest.raises(prompts.ContextNotRepresentable) as caught:
        request_for(**changes)
    assert caught.value.code == "context_limit"


def test_the_case_identity_changes_with_every_pinned_fact():
    base = dict(rulebook_id="book", rulebook_version=2, rule_id="R", sample_id="s", sample_revision=3,
                image_sha256="a" * 64, annotation_sha256="b" * 64)
    reference = prompts.case_identity(**base)
    assert len(reference) == 64
    for field, other in (("rulebook_version", 3), ("sample_revision", 4), ("image_sha256", "c" * 64), ("annotation_sha256", "d" * 64), ("sample_id", "t")):
        assert prompts.case_identity(**{**base, field: other}) != reference
