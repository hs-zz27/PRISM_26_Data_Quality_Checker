"""Target-focused evidence views: key mapping, shared crop window, coordinate transforms, context.

Pure rendering on SYNTHETIC images; no model, database or importer.
"""
from io import BytesIO
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from backend.app.services.target_focus import (
    MARGIN_FRACTION, MAX_CROP_SCALE, MIN_MARGIN_PX, MIN_WINDOW_PX, TARGET_COLOR, focus_window, render_focus)

W, H = 400, 600
PERSON = (100, 150, 160, 450)            # left, top, right, bottom of the bright "person" block
PERSON_COLOR = (250, 240, 40)


def scene(size=(W, H)):
    image = Image.new("RGB", size, (60, 80, 60))
    ImageDraw.Draw(image).rectangle(PERSON, fill=PERSON_COLOR)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def polygon(key, object_id, points, origin='derived_from_polygon'):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return {'source_key': key, 'object_id': object_id, 'label': 'person', 'bbox': [min(xs), min(ys), max(xs), max(ys)],
            'bbox_origin': origin, 'polygons': [points]}


def around(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


PERSON_POLYGON = around(*PERSON)
NEIGHBOUR_POLYGON = around(250, 160, 320, 440)


def open_png(data):
    return Image.open(BytesIO(data)).convert('RGB')


def near(image, x, y, color, tolerance=40, radius=3):
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            px, py = int(x) + dx, int(y) + dy
            if 0 <= px < image.width and 0 <= py < image.height:
                pixel = image.getpixel((px, py))
                if all(abs(a - b) <= tolerance for a, b in zip(pixel, color)):
                    return True
    return False


MAGENTA, CYAN = (255, 0, 255), (0, 255, 255)


def test_the_target_is_highlighted_by_its_stable_key_not_by_its_position_or_display_number():
    a, b = polygon('/shapes/0', 1, PERSON_POLYGON), polygon('/shapes/1', 2, NEIGHBOUR_POLYGON)
    for target, other in (('/shapes/0', '/shapes/1'), ('/shapes/1', '/shapes/0')):
        overlay = open_png(render_focus(scene(), [a, b], target, 'labelme').overlay_full)
        mids = {'/shapes/0': (130, 150), '/shapes/1': (285, 160)}          # midpoints of each polygon's top edge
        assert near(overlay, *mids[target], MAGENTA) and not near(overlay, *mids[target], CYAN)
        assert near(overlay, *mids[other], CYAN) and not near(overlay, *mids[other], MAGENTA)
    # Swap the display numbers: the highlight must still follow the key.
    swapped = [polygon('/shapes/0', 2, PERSON_POLYGON), polygon('/shapes/1', 1, NEIGHBOUR_POLYGON)]
    overlay = open_png(render_focus(scene(), swapped, '/shapes/0', 'labelme').overlay_full)
    assert near(overlay, 130, 150, MAGENTA) and near(overlay, 285, 160, CYAN)


def test_original_and_overlay_crops_use_one_window_and_differ_only_where_annotations_are_drawn():
    objects = [polygon('/shapes/0', 1, PERSON_POLYGON), polygon('/shapes/1', 2, NEIGHBOUR_POLYGON)]
    views = render_focus(scene(), objects, '/shapes/0', 'labelme')
    original, overlay = open_png(views.original_crop), open_png(views.overlay_crop)
    assert original.size == overlay.size == views.window.size
    differing = [(x, y) for x in range(original.width) for y in range(original.height)
                 if original.getpixel((x, y)) != overlay.getpixel((x, y))]
    assert differing, 'the overlay crop must show the annotations'
    assert len(differing) < 0.15 * original.width * original.height
    for cx, cy in differing[::7]:                       # every difference lies on some annotation's outline
        x, y = views.window.to_original(cx, cy)
        on_a = PERSON[0] - 6 <= x <= PERSON[2] + 6 and PERSON[1] - 6 <= y <= PERSON[3] + 6
        on_b = 244 <= x <= 326 and 154 <= y <= 446
        assert on_a or on_b


def test_the_crop_to_original_transform_round_trips_and_lands_on_real_landmarks():
    window = render_focus(scene(), [polygon('/shapes/0', 1, PERSON_POLYGON)], '/shapes/0', 'labelme').window
    for x, y in ((120.5, 200.25), (399, 599), (window.x0, window.y0)):
        back = window.to_original(*window.to_crop(x, y))
        assert back == pytest.approx((x, y), abs=1e-6)
    views = render_focus(scene(), [polygon('/shapes/0', 1, PERSON_POLYGON)], '/shapes/0', 'labelme')
    crop = open_png(views.original_crop)
    cx, cy = views.window.to_crop(PERSON[0] + 5, PERSON[1] + 5)            # five pixels inside the person block
    assert all(abs(a - b) < 12 for a, b in zip(crop.getpixel((round(cx), round(cy))), PERSON_COLOR))
    cx, cy = views.window.to_crop(PERSON[0] - 8, PERSON[1] - 8)            # just outside it
    assert not all(abs(a - b) < 12 for a, b in zip(crop.getpixel((round(cx), round(cy))), PERSON_COLOR))


def test_a_displaced_polygon_keeps_the_person_it_should_enclose_inside_the_window():
    # A 10%-of-width shift, like the evaluation pair; the guarantee is a shift up to the window margin.
    shift = 0.10 * W
    displaced = polygon('/shapes/0', 1, around(PERSON[0] + shift, PERSON[1], PERSON[2] + shift, PERSON[3]))
    window = focus_window(displaced, (W, H), 'labelme')
    assert window.x0 <= PERSON[0] and window.x1 >= PERSON[2] + shift          # person AND displaced polygon inside
    assert window.y0 <= PERSON[1] and window.y1 >= PERSON[3]
    views = render_focus(scene(), [displaced], '/shapes/0', 'labelme')
    crop = open_png(views.original_crop)
    cx, cy = views.window.to_crop((PERSON[0] + PERSON[2]) / 2, (PERSON[1] + PERSON[3]) / 2)
    assert all(abs(a - b) < 12 for a, b in zip(crop.getpixel((round(cx), round(cy))), PERSON_COLOR))


def test_the_documented_limit_of_the_crop_margin_is_explicit_and_the_full_image_still_shows_the_person():
    margin = max(MARGIN_FRACTION * (PERSON[2] - PERSON[0]), MIN_MARGIN_PX)
    far = polygon('/shapes/0', 1, around(PERSON[0] + margin + 60, PERSON[1], PERSON[2] + margin + 60, PERSON[3]))
    window = focus_window(far, (W, H), 'labelme')
    assert window.x0 > PERSON[0], 'beyond the margin the person is outside the crop; this is the documented limit'
    full = open_png(render_focus(scene(), [far], '/shapes/0', 'labelme').overlay_full)
    assert full.getpixel((PERSON[0] + 20, PERSON[1] + 40)) == PERSON_COLOR      # the full overlay keeps the whole scene


def test_a_small_target_still_gets_the_minimum_context_margin_on_every_side():
    window = focus_window(polygon('/shapes/0', 1, around(1000, 2000, 1040, 2040)), (3000, 4000), 'labelme')
    assert (window.x0, window.y0, window.x1, window.y1) == (1000 - MIN_MARGIN_PX, 2000 - MIN_MARGIN_PX,
                                                            1040 + MIN_MARGIN_PX, 2040 + MIN_MARGIN_PX)


def test_the_margin_grows_with_a_large_target_instead_of_staying_at_the_minimum():
    big = (3000, 4000)
    target = polygon('/shapes/0', 1, around(1000, 1000, 1800, 3000))            # 800 x 2000 px
    window = focus_window(target, big, 'labelme')
    assert (window.x0, window.x1) == (1000 - 400, 1800 + 400)                   # 0.5 x width, well above the 96 px floor
    assert (window.y0, window.y1) == (1000 - 1000, 3000 + 1000)


def test_the_window_depends_only_on_the_targets_supplied_geometry_and_the_image_size():
    target = polygon('/shapes/0', 1, PERSON_POLYGON)
    alone = focus_window(target, (W, H), 'labelme')
    with_context = render_focus(scene(), [target, polygon('/shapes/1', 2, NEIGHBOUR_POLYGON)], '/shapes/0', 'labelme').window
    shifted_neighbour = render_focus(scene(), [target, polygon('/shapes/1', 2, around(5, 5, 60, 90))], '/shapes/0', 'labelme').window
    assert alone == with_context == shifted_neighbour


@pytest.mark.parametrize('points,edges', [(around(0, 100, 50, 300), ['left']), (around(350, 100, 400, 300), ['right']),
                                          (around(120, 0, 220, 60), ['top']), (around(120, 560, 220, 600), ['bottom']),
                                          (around(150, 250, 250, 350), [])])
def test_image_edges_are_reported_only_where_the_crop_really_reaches_them(points, edges):
    window = focus_window(polygon('/shapes/0', 1, points), (W, H), 'labelme')
    assert set(edges) <= set(window.edges)
    if not edges:
        assert window.edges == []
    assert window.document()['crop_edges_touching_image_edge'] == window.edges


def test_window_size_and_scale_stay_bounded():
    tiny = focus_window(polygon('/shapes/0', 1, around(10, 10, 14, 14)), (150, 100), 'labelme')
    assert tiny.x1 - tiny.x0 <= 150 and tiny.y1 - tiny.y0 <= 100 and tiny.scale <= MAX_CROP_SCALE
    small = focus_window(polygon('/shapes/0', 1, around(190, 290, 200, 300)), (W, H), 'labelme')
    assert small.x1 - small.x0 >= MIN_WINDOW_PX and small.y1 - small.y0 >= MIN_WINDOW_PX
    huge = focus_window(polygon('/shapes/0', 1, around(0, 0, 4000, 6000)), (4000, 6000), 'labelme')
    assert max(huge.size) <= 770 and huge.scale < 1 and huge.edges == ['left', 'top', 'right', 'bottom']


def test_a_one_based_penn_fudan_box_is_drawn_on_the_pixels_the_overlay_convention_uses():
    box = {'object_id': 1, 'label': 'person', 'bbox': [101, 151, 160, 450], 'polygons': []}   # one-based inclusive
    overlay = open_png(render_focus(scene(), [box], 'penn_fudan:1', 'penn_fudan').overlay_full)
    # One-based x=101 is pixel column 100; the 3-pixel outline is drawn inward (columns 100..102), none at column 99.
    assert near(overlay, 100, 300, MAGENTA, radius=0) and not near(overlay, 99, 300, MAGENTA, radius=0)
    assert not near(overlay, 104, 300, MAGENTA, radius=0)


def test_a_polygon_derived_box_is_never_drawn_as_a_box():
    only_polygon = polygon('/shapes/0', 1, [[150, 150], [250, 200], [150, 300]])
    overlay = open_png(render_focus(scene(), [only_polygon], '/shapes/0', 'labelme').overlay_full)
    # The derived bbox corner (250, 150) lies away from the outline; nothing is drawn there.
    assert not near(overlay, 250, 150, MAGENTA, radius=2)
    assert near(overlay, 200, 175, MAGENTA, radius=3)


def test_an_unknown_target_key_is_rejected():
    with pytest.raises(ValueError):
        render_focus(scene(), [polygon('/shapes/0', 1, PERSON_POLYGON)], '/shapes/9', 'labelme')


@pytest.mark.parametrize('geometry', [('box',), ('polygon',), ('box', 'polygon')])
def test_only_selected_geometry_is_magenta_on_a_coco_object_with_both(geometry):
    obj = polygon('/annotations/0', 1, [[100, 150], [160, 200], [100, 450]], origin='supplied')
    obj['bbox'] = [80, 130, 180, 420]
    objects = [obj, polygon('/annotations/1', 2, NEIGHBOUR_POLYGON)]
    before = deepcopy(objects)
    image = scene()
    views = render_focus(image, objects, '/annotations/0', 'coco', geometry=geometry)
    overlay = open_png(views.overlay_full)
    box_color = MAGENTA if 'box' in geometry else (255, 221, 0)
    polygon_color = MAGENTA if 'polygon' in geometry else CYAN
    assert near(overlay, 130, 130, box_color, tolerance=0, radius=0)
    assert near(overlay, 130, 175, polygon_color, tolerance=0, radius=0)
    if 'box' not in geometry:
        assert not near(overlay, 130, 130, MAGENTA)
    if 'polygon' not in geometry:
        assert not near(overlay, 130, 175, MAGENTA)
    assert near(overlay, 285, 160, CYAN) and not near(overlay, 285, 160, MAGENTA)
    assert open_png(views.original_crop).size == open_png(views.overlay_crop).size == views.window.size
    assert objects == before and image == scene()


def test_rule_specific_crop_ignores_other_geometry_of_the_same_object():
    obj = polygon('/annotations/0', 1, around(2000, 2100, 2200, 2300), origin='supplied')
    obj['bbox'] = [1000, 1000, 1100, 1200]
    box_view = focus_window(obj, (3000, 4000), 'coco', geometry=('box',))
    polygon_view = focus_window(obj, (3000, 4000), 'coco', geometry=('polygon',))
    changed_polygon = deepcopy(obj)
    changed_polygon['polygons'] = [around(10, 10, 50, 50)]
    changed_box = {**obj, 'bbox': [20, 20, 60, 60]}
    assert box_view == focus_window(changed_polygon, (3000, 4000), 'coco', geometry=('box',))
    assert polygon_view == focus_window(changed_box, (3000, 4000), 'coco', geometry=('polygon',))
    assert box_view != polygon_view
    assert box_view.x0 <= 1000 and box_view.x1 >= 1100
    assert polygon_view.x0 <= 2000 and polygon_view.x1 >= 2200


@pytest.mark.parametrize('geometry', [(), ('mask',), ('box', 'box'), ('box',)])
def test_invalid_or_unsupplied_selected_geometry_cannot_fall_back_to_a_derived_box(geometry):
    obj = polygon('/shapes/0', 1, PERSON_POLYGON)
    with pytest.raises(ValueError):
        render_focus(scene(), [obj], '/shapes/0', 'labelme', geometry=geometry)


def test_selected_box_preserves_penn_fudan_one_based_conversion():
    obj = {'object_id': 1, 'label': 'person', 'bbox': [101, 151, 160, 450], 'polygons': []}
    legacy = render_focus(scene(), [obj], 'penn_fudan:1', 'penn_fudan')
    selected = render_focus(scene(), [obj], 'penn_fudan:1', 'penn_fudan', geometry=('box',))
    assert selected == legacy
