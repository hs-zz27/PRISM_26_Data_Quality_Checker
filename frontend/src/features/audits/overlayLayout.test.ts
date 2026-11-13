import { describe, expect, it } from 'vitest';
import {
  badgeWidth, boundarySegments, drawsBox, isBoxCheck, placeBadges, segmentCrossesRect, BADGE_HEIGHT,
  type BadgeInput, type OverlayShape,
} from './overlayLayout';

const person = (left: number, top = 40, width = 40, height = 120): number[][] => [
  [left + width / 2 - 6, top], [left + width / 2 + 6, top], [left + width, top + 30], [left + width, top + height],
  [left, top + height], [left, top + 30],
];
const bounds = (points: number[][]) => ({
  x0: Math.min(...points.map((p) => p[0]!)), y0: Math.min(...points.map((p) => p[1]!)),
  x1: Math.max(...points.map((p) => p[0]!)), y1: Math.max(...points.map((p) => p[1]!)),
});

function scene(polygons: number[][][]) {
  const shapes: OverlayShape[] = polygons.map((points) => ({ polygons: [points], box: null }));
  const inputs: BadgeInput[] = polygons.map((points, index) => ({
    displayNumber: index + 1, text: `#${index + 1} /shapes/${index}`, bounds: bounds(points),
  }));
  return { shapes, inputs, segments: boundarySegments(shapes) };
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: typeof a) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

describe('which objects get a box drawn', () => {
  it('never draws a box for an object that has a polygon', () => {
    expect(drawsBox({ bbox: [10, 10, 50, 90], polygons: [person(10)], bbox_origin: 'derived_from_polygon' }, false)).toBe(false);
    expect(drawsBox({ bbox: [10, 10, 50, 90], polygons: [person(10)], bbox_origin: 'supplied' }, false)).toBe(false);
  });

  it('draws the box of an object whose only geometry is a box', () => {
    expect(drawsBox({ bbox: [10, 10, 50, 90] }, false)).toBe(true);
    expect(drawsBox({ bbox: [10, 10, 50, 90], polygons: [] }, false)).toBe(true);
    expect(drawsBox({ bbox: null, polygons: [person(10)] }, false)).toBe(false);
  });

  it('still shows a supplied box that a saved box finding is about, but never a worked-out one', () => {
    expect(drawsBox({ bbox: [10, 10, 50, 90], polygons: [person(10)], bbox_origin: 'supplied' }, true)).toBe(true);
    expect(drawsBox({ bbox: [10, 10, 50, 90], polygons: [person(10)], bbox_origin: 'derived_from_polygon' }, true)).toBe(false);
  });

  it('recognises the box checks by name', () => {
    for (const name of ['box_validity', 'box_overlaps', 'box_mask_alignment', 'mask_outside_box', 'mask_box_fill', 'pedestrian_box_enclosure']) {
      expect(isBoxCheck(name)).toBe(true);
    }
    for (const name of ['person_coverage', 'allowed_labels', 'polygon_validity', 'unannotated_people', 'annotation_guideline', null, undefined]) {
      expect(isBoxCheck(name)).toBe(false);
    }
  });
});

describe('segmentCrossesRect', () => {
  const rect = { x: 10, y: 10, width: 20, height: 10 };
  it('detects a line through, into, inside or along the rectangle', () => {
    expect(segmentCrossesRect({ x0: 0, y0: 15, x1: 50, y1: 15 }, rect)).toBe(true);
    expect(segmentCrossesRect({ x0: 15, y0: 0, x1: 15, y1: 14 }, rect)).toBe(true);
    expect(segmentCrossesRect({ x0: 12, y0: 12, x1: 14, y1: 14 }, rect)).toBe(true);
    expect(segmentCrossesRect({ x0: 0, y0: 0, x1: 40, y1: 30 }, rect)).toBe(true);
  });
  it('ignores a line that passes beside it', () => {
    expect(segmentCrossesRect({ x0: 0, y0: 0, x1: 50, y1: 0 }, rect)).toBe(false);
    expect(segmentCrossesRect({ x0: 40, y0: 0, x1: 40, y1: 50 }, rect)).toBe(false);
    expect(segmentCrossesRect({ x0: 0, y0: 30, x1: 5, y1: 0 }, rect)).toBe(false);
  });
});

describe('placeBadges', () => {
  const scenes: Array<[string, number[][][], number, number]> = [
    ['one person', [person(100)], 400, 300],
    ['ten people side by side', Array.from({ length: 10 }, (_, index) => person(20 + index * 60)), 640, 300],
    ['three overlapping people', [person(100), person(120), person(140)], 400, 300],
    ['people against the top-left corner', [person(0, 0), person(45, 0)], 400, 300],
    ['a small person inside a large one', [person(100, 40, 200, 200), person(180, 120, 20, 40)], 400, 300],
  ];

  it.each(scenes)('keeps every label off every outline: %s', (_name, polygons, width, height) => {
    const { inputs, segments } = scene(polygons);
    const placed = placeBadges(inputs, width, height, segments);
    expect(placed).toHaveLength(polygons.length);
    for (const badge of placed) {
      expect(badge.x).toBeGreaterThanOrEqual(0);
      expect(badge.y).toBeGreaterThanOrEqual(0);
      expect(badge.x + badge.width).toBeLessThanOrEqual(width);
      expect(badge.y + badge.height).toBeLessThanOrEqual(height);
      expect(badge.width).toBe(badgeWidth(badge.text));
      expect(badge.height).toBe(BADGE_HEIGHT);
      expect(segments.filter((segment) => segmentCrossesRect(segment, badge))).toEqual([]);
    }
    for (const [index, badge] of placed.entries()) {
      for (const other of placed.slice(index + 1)) expect(overlaps(badge, other)).toBe(false);
    }
  });

  it('joins a label to its object with a leader line when the label sits away from it', () => {
    const tiny = [[200, 150], [210, 150], [210, 160], [200, 160]];
    const { inputs, segments } = scene(Array.from({ length: 12 }, () => tiny));
    const placed = placeBadges(inputs, 400, 300, segments);
    expect(placed.some((badge) => badge.leaderTarget != null)).toBe(true);
  });

  it('is stable: the same scene always gives the same places', () => {
    const { inputs, segments } = scene(scenes[1]![1]);
    expect(placeBadges(inputs, 640, 300, segments)).toEqual(placeBadges(inputs, 640, 300, segments));
  });

  it('still places a label when no free spot exists', () => {
    const crowded = Array.from({ length: 6 }, () => [[0, 0], [60, 0], [60, 24], [0, 24]]);
    const { inputs, segments } = scene(crowded);
    expect(placeBadges(inputs, 60, 24, segments)).toHaveLength(6);
  });
});

describe('boundarySegments', () => {
  it('closes each polygon and adds the four sides of a drawn box', () => {
    const segments = boundarySegments([
      { polygons: [[[0, 0], [10, 0], [10, 10]]], box: null },
      { polygons: [], box: { left: 20, top: 20, width: 10, height: 5 } },
    ]);
    expect(segments).toHaveLength(3 + 4);
    expect(segments[2]).toEqual({ x0: 10, y0: 10, x1: 0, y1: 0 });
    expect(segments.slice(3)).toContainEqual({ x0: 20, y0: 25, x1: 30, y1: 25 });
  });
});
