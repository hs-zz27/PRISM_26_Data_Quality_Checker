"""How closely a person's outline follows the person's visible boundary in the photo.

The person check compares an outline with a model's outline of the person, which is too coarse to tell a tight
polygon from a loose one. This measures the outline against the photo itself: a tight outline runs along the
place where the person meets the background or another object, and that place is an edge in the photo. The
measure is the share of the outline that lies on such an edge, and the stretches that do not are reported as
``mismatch`` regions (see "Finding regions" in backend/api/AUDITS.md).

Soft boundaries (dark clothes on a dark background, hair, blur) give weak edges, so edges are looked for at
two blur levels, and what counts as an edge is set from each photo's own spread of contrast rather than one
fixed strength. Colour edges and contrast boosting were tried and left out: they find edges almost
everywhere, which hides loose outlines (see evaluation/boundary_fit/RESULTS.md).
Where the background is busy enough that almost any line lies on an edge, the measure cannot tell and says
so (``busy``); such an outline goes to a reviewer, never to a pass.

Pure: no I/O, no model, no GPU. The photo and the annotation are never changed. Nothing here decides an
audit; the pass mark is provisional until reference outlines agreed with the mentor exist.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

SCALES = (0.8, 2.0)            # blur levels, in pixels: sharp edges, then soft ones
HIGH_PERCENTILE = 85           # a strong edge is among the photo's strongest 15% of brightness changes ...
MIN_HIGH = 12.0                # ... but never weaker than this, so a flat photo does not invent edges
LOW_SHARE = 0.5                # a weaker edge counts where it continues a strong one
MIN_TOLERANCE_PX = 2           # an outline within this many pixels of an edge is on it ...
TOLERANCE_FRACTION = 0.008     # ... or within this share of the person's height, if larger
STEP_PX = 1.0                  # the outline is sampled every pixel
BORDER_PX = 3                  # stretches this close to the photo's border cannot be judged
MIN_BLIP_PX = 5                # an isolated touch of an edge shorter than this does not break a stretch
MIN_STRETCH_PX = 12            # a reported stretch is at least this long ...
MIN_STRETCH_FRACTION = 0.05    # ... or this share of the person's height, if longer
BUSY_SHIFT_FRACTION = 0.03     # the busy test moves the outline outward by this share of the height
BUSY_SHARE = 0.80              # if the moved outline still lies on edges this often, the background is busy

# Provisional pass mark: halfway between the mentor's weaker tight outline (93.5%) and the best outline he
# rejected (87.5%) on 18 Nov 2026. Two tight examples are not enough; set it again from agreed reference
# outlines before any audit uses it.
PROVISIONAL_MIN_SHARE = 0.90


@dataclass(frozen=True)
class BoundaryFit:
    share_on_edge: float | None   # of the judged outline; None when nothing could be judged
    judged_px: int                # outline length judged
    border_px: int                # outline length along the photo's border, not judged
    busy_share: float | None      # how often the same outline moved outward still lies on edges
    tolerance_px: float
    regions: list[dict] = field(default_factory=list)

    @property
    def busy(self) -> bool:
        return self.busy_share is not None and self.busy_share >= BUSY_SHARE


def edge_distance(image: np.ndarray) -> np.ndarray:
    """Distance in pixels from every pixel to the nearest edge in the photo (RGB uint8, HxWx3)."""
    grey = cv2.cvtColor(np.ascontiguousarray(image), cv2.COLOR_RGB2GRAY)
    edges = np.zeros(grey.shape, np.uint8)
    for sigma in SCALES:
        blurred = cv2.GaussianBlur(grey, (0, 0), sigma)
        strength = (np.abs(cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3))
                    + np.abs(cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)))
        high = max(float(np.percentile(strength, HIGH_PERCENTILE)), MIN_HIGH)
        edges |= cv2.Canny(blurred, LOW_SHARE * high, high)
    return cv2.distanceTransform(255 - edges, cv2.DIST_L2, 5)


def _samples(polygon) -> np.ndarray:
    points = np.asarray(polygon, float)
    out = []
    for start, end in zip(points, np.roll(points, -1, axis=0)):
        count = max(1, int(np.ceil(np.hypot(*(end - start)) / STEP_PX)))
        out.append(start + (end - start) * (np.arange(count)[:, None] / count))
    return np.vstack(out)


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """(start, length) of each run of True in a closed loop of flags."""
    if flags.all():
        return [(0, len(flags))]
    shift = int(np.argmin(flags))  # start the walk on a False so no run wraps
    rolled = np.roll(flags, -shift)
    runs, start = [], None
    for index, value in enumerate(np.append(rolled, False)):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append(((start + shift) % len(flags), index - start))
            start = None
    return runs


def _side(box, person_box) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    x0, y0, x1, y1 = person_box
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    if cy < y0 + h / 4:
        return "top"
    if cy > y1 - h / 4:
        return "bottom"
    if cx < x0 + w / 3:
        return "left"
    if cx > x1 - w / 3:
        return "right"
    return "middle"


def _moved_outward(polygons, size, distance_px):
    mask = np.zeros((size[1], size[0]), np.uint8)
    cv2.fillPoly(mask, [np.rint(np.asarray(p, float)).astype(np.int32) for p in polygons], 1)
    grown = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * distance_px + 1,) * 2))
    contours, _ = cv2.findContours(grown, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return [c.reshape(-1, 2) for c in contours if len(c) >= 3]


def boundary_fit(distance: np.ndarray, polygons) -> BoundaryFit:
    """The fit of one person's outline. ``polygons`` are all the parts drawn for that person.

    ``distance`` comes from ``edge_distance`` for the same photo, so it is computed once per photo.
    """
    height_px, width_px = distance.shape
    parts = [np.asarray(p, float) for p in polygons if len(p) >= 3]
    if not parts:
        return BoundaryFit(None, 0, 0, None, float(MIN_TOLERANCE_PX))
    every = np.vstack(parts)
    person_box = [float(every[:, 0].min()), float(every[:, 1].min()), float(every[:, 0].max()), float(every[:, 1].max())]
    person_height = person_box[3] - person_box[1]
    tolerance = max(float(MIN_TOLERANCE_PX), TOLERANCE_FRACTION * person_height)
    min_stretch = max(float(MIN_STRETCH_PX), MIN_STRETCH_FRACTION * person_height)
    judged = on = border = 0
    regions = []
    for part in parts:
        points = _samples(part)
        x = np.clip(np.rint(points[:, 0]).astype(int), 0, width_px - 1)
        y = np.clip(np.rint(points[:, 1]).astype(int), 0, height_px - 1)
        inside = (x >= BORDER_PX) & (y >= BORDER_PX) & (x < width_px - BORDER_PX) & (y < height_px - BORDER_PX)
        near = distance[y, x] <= tolerance
        border += int((~inside).sum())
        judged += int(inside.sum())
        on += int((near & inside).sum())
        # An edge touched for only a few pixels inside a long off-edge stretch is crossing it, not followed.
        for start, length in _runs(near):
            if length < MIN_BLIP_PX:
                near[(start + np.arange(length)) % len(near)] = False
        for start, length in _runs(~near & inside):
            if length * STEP_PX >= min_stretch:
                stretch = points[(start + np.arange(length)) % len(points)]
                box = [int(np.floor(stretch[:, 0].min())), int(np.floor(stretch[:, 1].min())),
                       int(np.ceil(stretch[:, 0].max())) + 1, int(np.ceil(stretch[:, 1].max())) + 1]
                regions.append({"kind": "mismatch", "side": _side(box, person_box), "box": box,
                                "length_px": int(round(length * STEP_PX))})
    busy_share = None
    shift = max(int(round(BUSY_SHIFT_FRACTION * person_height)), 3)
    moved = _moved_outward(parts, (width_px, height_px), shift)
    if moved:
        points = np.vstack([_samples(m) for m in moved])
        x, y = np.rint(points[:, 0]).astype(int), np.rint(points[:, 1]).astype(int)
        keep = (x >= BORDER_PX) & (y >= BORDER_PX) & (x < width_px - BORDER_PX) & (y < height_px - BORDER_PX)
        if keep.any():
            busy_share = round(float((distance[y[keep], x[keep]] <= tolerance).mean()), 4)
    share = round(on / judged, 4) if judged else None
    regions.sort(key=lambda region: -region["length_px"])
    return BoundaryFit(share, judged, border, busy_share, round(tolerance, 2), regions)


def provisional_outcome(fit: BoundaryFit, min_share: float = PROVISIONAL_MIN_SHARE) -> str:
    """``pass``, ``fail`` or ``unsure``. Unsure when nothing could be judged or the background is too busy."""
    if fit.share_on_edge is None or fit.busy:
        return "unsure"
    return "pass" if fit.share_on_edge >= min_share else "fail"
