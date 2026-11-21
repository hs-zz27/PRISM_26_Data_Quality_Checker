"""How far a person's outline lies from a reference outline of the same person.

The person check compares area with a coarse model mask, which cannot tell a tight polygon from one a few
pixels loose all round: on a large person the loose band is a small share of the area. This compares the two
boundaries instead, in pixels, in both directions:

- **outward**: how far the annotation reaches beyond the person (a loose outline, or one that takes in a
  neighbour or the background);
- **inward**: how far the person reaches beyond the annotation (an outline that cuts in or leaves a part out).

Each direction is reported as the distance that 95% of the boundary stays within, the largest distance, and
the share of the boundary farther away than a tolerance. The places farther away than the tolerance are
reported as ``extra`` and ``missing`` regions in the review screen's format (see "Finding regions" in
backend/api/AUDITS.md). Boundary IoU (Cheng et al., 2021) at the same
tolerance and the plain mask IoU are reported too; the smaller of the two is the overall agreement, as the
Boundary IoU authors recommend, so that an odd shape cannot score well on either alone.

The tolerance is in pixels and should come from how far careful outlines of the same person differ, which is
measured from the mentor's examples (evaluation/tight_reference/distances.py), not from the person's height.
A person only a few tolerances tall cannot be judged this way and is marked ``too_small``.

Pure: no I/O, no model, no GPU. Nothing here decides an audit, and the reference must itself be checked
(a machine outline is a witness with its own error, not ground truth).
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from .boundary_fit import _side

PERCENTILE = 95            # the boundary distance reported is the one this share of the boundary stays within
MIN_HEIGHT_TOLERANCES = 8  # a reference less than this many tolerances tall is too small to judge
MIN_REGION_TOLERANCES = 4  # a region smaller than this many tolerances squared is not reported


@dataclass(frozen=True)
class BoundaryDistance:
    tolerance_px: float
    outward_px: float          # 95% of the annotation's boundary lies within this distance of the reference
    outward_max_px: float
    outward_share: float       # share of the annotation's boundary farther than the tolerance outside the reference
    inward_px: float           # 95% of the reference's boundary lies within this distance of the annotation
    inward_max_px: float
    inward_share: float        # share of the reference's boundary farther than the tolerance outside the annotation
    mask_iou: float
    boundary_iou: float
    reference_height_px: int
    regions: list[dict] = field(default_factory=list)

    @property
    def agreement(self) -> float:
        """min(mask IoU, Boundary IoU)."""
        return min(self.mask_iou, self.boundary_iou)

    @property
    def too_small(self) -> bool:
        return self.reference_height_px < MIN_HEIGHT_TOLERANCES * self.tolerance_px


def polygons_mask(polygons, size: tuple[int, int]) -> np.ndarray:
    """One boolean mask (height x width) for all of a person's polygons; ``size`` is (width, height)."""
    mask = np.zeros((size[1], size[0]), np.uint8)
    for polygon in polygons:
        points = np.rint(np.asarray(polygon, float)).astype(np.int32)
        if len(points) >= 3:
            cv2.fillPoly(mask, [points], 1)
    return mask.astype(bool)


def _distance_to(mask: np.ndarray) -> np.ndarray:
    """Distance in pixels from every pixel to the nearest pixel of ``mask`` (0 inside it)."""
    return cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5)


def _depth(mask: np.ndarray) -> np.ndarray:
    """Distance from every pixel of ``mask`` to the nearest pixel outside it; the photo's border counts as outside."""
    padded = np.pad(mask.astype(np.uint8), 1)
    return cv2.distanceTransform(padded, cv2.DIST_L2, 5)[1:-1, 1:-1]


def _boundary(mask: np.ndarray) -> np.ndarray:
    """The mask's own boundary pixels (inside the mask, touching the outside or the photo's border)."""
    depth = _depth(mask)
    return mask & (depth <= 1.0)


def _one_side(boundary: np.ndarray, distance: np.ndarray, tolerance: float):
    values = distance[boundary]
    if not len(values):
        return 0.0, 0.0, 0.0
    return (float(np.percentile(values, PERCENTILE)), float(values.max()), float(np.mean(values > tolerance)))


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b) / union) if union else 1.0


def _regions(area: np.ndarray, kind: str, person_box, min_area: float) -> list[dict]:
    count, _, stats, _ = cv2.connectedComponentsWithStats(area.astype(np.uint8), connectivity=8)
    out = []
    for x, y, w, h, pixels in stats[1:count]:
        if pixels >= min_area:
            box = [int(x), int(y), int(x + w), int(y + h)]
            out.append({"kind": kind, "side": _side(box, person_box), "box": box, "area_px": int(pixels)})
    return out


def boundary_distance(annotation: np.ndarray, reference: np.ndarray, tolerance_px: float) -> BoundaryDistance:
    """Compare an annotation mask with a reference mask of the same person (both boolean, same size)."""
    if annotation.shape != reference.shape:
        raise ValueError("The annotation and the reference must be the same size.")
    if tolerance_px <= 0:
        raise ValueError("The tolerance must be positive.")
    if not reference.any():
        raise ValueError("The reference is empty.")
    annotation, reference = annotation.astype(bool), reference.astype(bool)
    to_reference, to_annotation = _distance_to(reference), _distance_to(annotation)
    outward = _one_side(_boundary(annotation), to_reference, tolerance_px)
    inward = _one_side(_boundary(reference), to_annotation, tolerance_px)
    band_a = annotation & (_depth(annotation) <= tolerance_px)
    band_r = reference & (_depth(reference) <= tolerance_px)
    rows, columns = np.flatnonzero(reference.any(axis=1)), np.flatnonzero(reference.any(axis=0))
    person_box = [int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1]
    min_area = MIN_REGION_TOLERANCES * tolerance_px ** 2
    regions = (_regions(annotation & (to_reference > tolerance_px), "extra", person_box, min_area)
               + _regions(reference & (to_annotation > tolerance_px), "missing", person_box, min_area))
    regions.sort(key=lambda region: -region["area_px"])
    return BoundaryDistance(
        tolerance_px=float(tolerance_px),
        outward_px=outward[0], outward_max_px=outward[1], outward_share=outward[2],
        inward_px=inward[0], inward_max_px=inward[1], inward_share=inward[2],
        mask_iou=_iou(annotation, reference), boundary_iou=_iou(band_a, band_r),
        reference_height_px=int(rows[-1] - rows[0] + 1), regions=regions)


def tolerance_for(size: tuple[int, int], diagonal_fraction: float) -> float:
    """A tolerance in pixels as a share of the photo's diagonal, the way published boundary measures set it."""
    return diagonal_fraction * float(np.hypot(*size))
