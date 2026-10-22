"""Coordinate and evidence checks using only synthetic pixels."""

from copy import deepcopy
from io import BytesIO

from PIL import Image
import pytest

from backend.experiments.box_grounding.views import RenderError, ViewLimits, render_view


def image_bytes():
    image = Image.new("RGB", (4, 3), "blue")
    image.putpixel((3, 2), (12, 34, 56))
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_crop_includes_single_pixel_at_right_bottom_source_edge():
    source = image_bytes()
    rendered = render_view(source, [{"bbox": [4, 3, 4, 3]}], (1, 1), ViewLimits(), "crops")
    with Image.open(BytesIO(rendered.data)) as sheet:
        assert sheet.size == (256, 288)
        assert sheet.getpixel((127, 155)) == (12, 34, 56)
    assert rendered.source_boxes == ((4, 3, 4, 3),)


def test_duplicate_object_ids_preserve_distinct_regions_without_modifying_source():
    source = image_bytes()
    objects = [{"object_id": 1, "bbox": [1, 1, 1, 1]},
               {"object_id": 1, "bbox": [4, 3, 4, 3]}]
    before = deepcopy(objects)
    rendered = render_view(source, objects, (1, 1), ViewLimits(), "crops")
    with Image.open(BytesIO(rendered.data)) as sheet:
        assert sheet.size == (512, 288)
        assert sheet.getpixel((127, 155)) == (0, 0, 255)
        assert sheet.getpixel((383, 155)) == (12, 34, 56)
    assert objects == before
    assert source == image_bytes()
    assert rendered.source_boxes == ((1, 1, 1, 1), (4, 3, 4, 3))


def test_overview_draws_inclusive_edges_on_a_copy():
    source = image_bytes()
    view = render_view(source, [{"bbox": [1, 1, 4, 3]}], (1, 1), ViewLimits(), "overview")
    with Image.open(BytesIO(view.data)) as overview:
        assert overview.size == (4, 3)
        assert overview.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(BytesIO(source)) as original:
        assert original.getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize("bbox", [
    [0, 1, 4, 3], [1, 1, 5, 3], [3, 1, 2, 3], [1, 1, 4, 4],
    [1.0, 1, 4, 3], [True, 1, 4, 3], [1, 1, 4], None,
])
def test_invalid_boxes_are_rejected_instead_of_clipped_or_dropped(bbox):
    with pytest.raises(RenderError):
        render_view(image_bytes(), [{"bbox": bbox}], (1, 1), ViewLimits(), "crops")


@pytest.mark.parametrize("objects,origin", [
    ([], (1, 1)), ([{"bbox": [1, 1, 4, 3]}] * 33, (1, 1)),
    ([{"bbox": [1, 1, 4, 3]}], (0, 0)),
])
def test_unrepresentable_scope_is_rejected(objects, origin):
    with pytest.raises(RenderError, match="unsupported_origin_or_box_count"):
        render_view(image_bytes(), objects, origin, ViewLimits(), "crops")


@pytest.mark.parametrize("limits,error", [
    (ViewLimits(max_image_bytes=1), "source_byte_limit"),
    (ViewLimits(max_pixels=11), "source_pixel_limit"),
    (ViewLimits(max_pixels=12), "rendered_pixel_limit"),
    (ViewLimits(max_image_bytes=200), "rendered_byte_limit"),
])
def test_source_and_rendered_limits_fail_explicitly(limits, error):
    with pytest.raises(RenderError, match=error):
        render_view(image_bytes(), [{"bbox": [1, 1, 4, 3]}], (1, 1), limits, "crops")
