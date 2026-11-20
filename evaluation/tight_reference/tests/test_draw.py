"""Machine-drawn tight outlines (draw.py), on synthetic photos."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

SPEC = importlib.util.spec_from_file_location("tight_reference_draw", Path(__file__).resolve().parents[1] / "draw.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)
H, W = 360, 480


def figure(cx=200, top=40):
    """A person-like shape: head, body and two legs, about 280 px tall."""
    mask = np.zeros((H, W), np.uint8)
    cv2.circle(mask, (cx, top + 30), 26, 1, -1)
    cv2.rectangle(mask, (cx - 40, top + 56), (cx + 40, top + 180), 1, -1)
    cv2.rectangle(mask, (cx - 38, top + 180), (cx - 8, top + 280), 1, -1)
    cv2.rectangle(mask, (cx + 8, top + 180), (cx + 38, top + 280), 1, -1)
    return mask


def photo(*masks_and_colours):
    rng = np.random.default_rng(3)
    image = (rng.normal(60, 6, (H, W, 3))).clip(0, 255).astype(np.uint8)
    for mask, colour in masks_and_colours:
        image[mask > 0] = colour
    return image


def loose(mask, grow=10):
    contours, _ = cv2.findContours(cv2.dilate(mask, d.disk(grow)), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [c.reshape(-1, 2).astype(float).tolist() for c in contours]


def iou(a, b):
    return np.logical_and(a, b).sum() / np.logical_or(a, b).sum()


def test_a_loose_outline_is_drawn_tight_around_the_person():
    person = figure()
    parts, report = d.tighten(photo((person, (200, 170, 150))), loose(person), np.zeros((H, W), np.uint8), person.astype(bool))
    drawn = d.raster(parts, (W, H))
    assert report["parts"] == 1 and iou(drawn, person) > 0.93 > iou(d.raster(loose(person), (W, H)), person)


def test_the_outline_stays_off_a_neighbour_drawn_inside_it():
    me, neighbour = figure(cx=170), figure(cx=262)
    outline = loose(me, grow=14)  # takes in part of the neighbour
    assert (d.raster(outline, (W, H)) & neighbour).sum() > 300
    parts, _ = d.tighten(photo((me, (200, 170, 150)), (neighbour, (90, 140, 210))), outline, neighbour, me.astype(bool))
    drawn = d.raster(parts, (W, H))
    assert (drawn & neighbour).sum() < 50 and iou(drawn, me) > 0.9


def test_a_person_split_by_someone_in_front_becomes_parts_with_one_group():
    behind, front = figure(cx=220), np.zeros((H, W), np.uint8)
    front[150:230, 120:330] = 1  # something across the person's middle
    visible = behind & (1 - front)
    document = {"imageWidth": W, "imageHeight": H, "shapes": [
        {"label": "person", "points": p, "group_id": 4, "description": "COCO annotation 11", "shape_type": "polygon", "flags": {}}
        for p in loose(visible, grow=6)]}
    found = SimpleNamespace(people=[SimpleNamespace(mask=visible.astype(bool))])
    redrawn, (report,) = d.draw_document(document, photo((behind, (200, 170, 150)), (front, (40, 90, 40))), found)
    pieces = cv2.connectedComponents(visible)[0] - 1  # upper body and each leg below the thing in front
    assert pieces == 3 and report["redrawn"] and report["parts"] == pieces
    assert {s["group_id"] for s in redrawn["shapes"]} == {4}
    assert {s["description"] for s in redrawn["shapes"]} == {"COCO annotation 11"}


def test_a_large_gap_inside_the_outline_is_cut_open_and_a_tiny_one_filled():
    ring = np.zeros((H, W), np.uint8)
    cv2.circle(ring, (240, 180), 120, 1, -1)
    cv2.circle(ring, (240, 180), 50, 0, -1)    # a large gap, like an arm on the hip
    cv2.circle(ring, (240, 285), 3, 0, -1)      # a speck, away from where the cut goes
    opened = d._open_holes(ring.copy(), min_area=200)
    assert opened[180, 240] == 0 and opened[285, 240] == 1
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    traced = d.raster([c.reshape(-1, 2).tolist() for c in contours], (W, H))
    assert traced[180, 240] == 0  # the outline goes around the gap through the cut


def test_a_result_that_strays_keeps_the_original_outline(monkeypatch):
    person = figure()
    document = {"imageWidth": W, "imageHeight": H, "shapes": [
        {"label": "person", "points": loose(person)[0], "group_id": None, "description": "COCO annotation 5",
         "shape_type": "polygon", "flags": {}},
        {"label": "car", "points": [[1, 1], [20, 1], [20, 20]], "group_id": None, "description": "",
         "shape_type": "polygon", "flags": {}}]}
    monkeypatch.setattr(d, "MIN_IOU", 1.01)
    redrawn, (report,) = d.draw_document(document, photo((person, (200, 170, 150))), None)
    assert not report["redrawn"] and report["reason"] == "strayed from COCO's person"
    assert sorted(s["label"] for s in redrawn["shapes"]) == ["car", "person"]
    assert next(s for s in redrawn["shapes"] if s["label"] == "person") == document["shapes"][0]
