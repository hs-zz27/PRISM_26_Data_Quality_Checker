"""Per-box crops: exact source pixels, inclusive edges, preserved identity and explicit rejection."""

from copy import deepcopy
from io import BytesIO

from PIL import Image
import pytest

from backend.experiments.box_grounding.crops import (
    CROP_PREPROCESSING,
    render_box_crops,
    validate_scope,
)
from backend.experiments.box_grounding.views import RenderError, ViewLimits

from box_helpers import BACKGROUND, COLORS, DEFAULT_SPECS, painted_png

SPEC = DEFAULT_SPECS[0]  # case-02: 12x9 image, three boxes, repeated object ID 1
OBJECTS = [dict(object_id=i, label=label, bbox=list(box)) for i, label, box in SPEC.boxes]


def crops():
    return render_box_crops(painted_png(SPEC), OBJECTS, (1, 1), ViewLimits())


def decode(crop):
    with Image.open(BytesIO(crop.png)) as image:
        return image.convert("RGB")


def colors(picture):
    return {picture.getpixel((x, y)) for x in range(picture.width) for y in range(picture.height)}


def test_crops_are_the_exact_source_pixels_with_no_padding_resize_or_overlay():
    for crop, color, (_, _, (xmin, ymin, xmax, ymax)) in zip(crops(), COLORS, SPEC.boxes):
        picture = decode(crop)
        assert picture.size == (xmax - xmin + 1, ymax - ymin + 1) == (crop.width, crop.height)
        assert colors(picture) == {color}  # only this box's pixels: no border, label or background
        assert crop.pil_box == (xmin - 1, ymin - 1, xmax, ymax)


def test_one_based_inclusive_right_and_bottom_edges_are_included():
    # case-02's last box ends exactly on the last column and row of the 12x9 image
    last = crops()[2]
    assert last.bbox == (9, 7, 12, 9) and (last.width, last.height) == (4, 3)
    assert decode(last).getpixel((3, 2)) == COLORS[2]  # the bottom-right source pixel
    single = render_box_crops(painted_png(SPEC), [dict(object_id=1, label="p", bbox=[12, 9, 12, 9])], (1, 1), ViewLimits())[0]
    assert (single.width, single.height) == (1, 1) and decode(single).getpixel((0, 0)) == COLORS[2]


def test_the_top_left_box_starts_at_pixel_one_not_zero_pixel_two():
    first = crops()[0]
    assert first.pil_box[:2] == (0, 0) and decode(first).getpixel((0, 0)) == COLORS[0]


def test_repeated_object_ids_keep_distinct_annotation_indices_and_regions():
    found = crops()
    assert [c.object_id for c in found] == [1, 1, 3]
    assert [c.annotation_index for c in found] == [0, 1, 2]
    assert [c.box_number for c in found] == [1, 2, 3]
    assert found[0].png_sha256 != found[1].png_sha256 and found[0].bbox != found[1].bbox


def test_rendering_never_changes_the_source_bytes_or_objects():
    source, objects = painted_png(SPEC), deepcopy(OBJECTS)
    render_box_crops(source, objects, (1, 1), ViewLimits())
    assert source == painted_png(SPEC) and objects == OBJECTS


def test_crops_are_deterministic_and_hash_their_png_bytes():
    from hashlib import sha256
    first, second = crops(), crops()
    assert [c.png_sha256 for c in first] == [c.png_sha256 for c in second]
    assert all(c.png_sha256 == sha256(c.png).hexdigest() for c in first)
    assert CROP_PREPROCESSING == "box-crop-png-v1"


def test_background_pixels_never_enter_a_crop():
    assert all(BACKGROUND not in colors(decode(c)) for c in crops())


@pytest.mark.parametrize("bbox", [
    [0, 1, 4, 3], [1, 1, 13, 3], [3, 1, 2, 3], [1, 1, 4, 10],
    [1.0, 1, 4, 3], [True, 1, 4, 3], [1, 1, 4], None,
])
def test_invalid_geometry_is_rejected_instead_of_clipped_or_dropped(bbox):
    objects = [dict(object_id=1, label="p", bbox=bbox)]
    with pytest.raises(RenderError):
        render_box_crops(painted_png(SPEC), objects, (1, 1), ViewLimits())
    with pytest.raises(RenderError):
        validate_scope(painted_png(SPEC), objects, (1, 1), ViewLimits())


@pytest.mark.parametrize("obj", [dict(bbox=[1, 1, 2, 2]), dict(object_id=True, label="p", bbox=[1, 1, 2, 2]),
                                 dict(object_id=1, label=" ", bbox=[1, 1, 2, 2])])
def test_missing_or_malformed_object_fields_are_rejected(obj):
    with pytest.raises(RenderError, match="invalid_object_fields"):
        render_box_crops(painted_png(SPEC), [obj], (1, 1), ViewLimits())


@pytest.mark.parametrize("objects,origin", [
    ([], (1, 1)),  # zero objects are never read as compliance
    ([dict(object_id=1, label="p", bbox=[1, 1, 2, 2])] * 33, (1, 1)),
    ([dict(object_id=1, label="p", bbox=[1, 1, 2, 2])], (0, 0)),
])
def test_zero_too_many_or_zero_based_scopes_are_rejected(objects, origin):
    with pytest.raises(RenderError, match="unsupported_origin_or_box_count"):
        render_box_crops(painted_png(SPEC), objects, origin, ViewLimits())


@pytest.mark.parametrize("limits,error", [
    (ViewLimits(max_image_bytes=1), "source_byte_limit"),
    (ViewLimits(max_pixels=107), "source_pixel_limit"),
    (ViewLimits(max_pixels=11), "source_pixel_limit"),
])
def test_source_limits_fail_explicitly(limits, error):
    with pytest.raises(RenderError, match=error):
        render_box_crops(painted_png(SPEC), OBJECTS, (1, 1), limits)


def test_a_crop_that_exceeds_the_rendered_byte_limit_is_rejected_not_truncated():
    import random
    noise = random.Random(1)
    gray = Image.new("L", (40, 40))
    gray.putdata([noise.randrange(256) for _ in range(40 * 40)])
    stream = BytesIO()
    gray.save(stream, format="PNG", optimize=True)
    source = stream.getvalue()
    whole = [dict(object_id=1, label="p", bbox=[1, 1, 40, 40])]
    unrestricted = render_box_crops(source, whole, (1, 1), ViewLimits())[0]
    assert len(unrestricted.png) > len(source)  # RGB re-encoding is larger than the compact source
    # The source passes its own byte limit; the larger rendered crop must be rejected, never shrunk.
    with pytest.raises(RenderError, match="rendered_byte_limit"):
        render_box_crops(source, whole, (1, 1), ViewLimits(max_image_bytes=len(source)))
