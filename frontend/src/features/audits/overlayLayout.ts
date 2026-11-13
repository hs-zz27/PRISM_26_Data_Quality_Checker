/**
 * Layout rules for the annotation overlay on the review image.
 *
 * - An object that has a polygon is drawn as that polygon only. Its box is not drawn, with one exception: a
 *   supplied box that a saved box-check finding is about stays visible, so old audits still show their evidence.
 * - A label never sits on an outline: it is placed where it crosses no drawn boundary and no other label, and is
 *   joined to its object with a leader line when it has to sit away from it.
 */

export interface Segment { x0: number; y0: number; x1: number; y1: number }
export interface Rect { x: number; y: number; width: number; height: number }
export interface Bounds { x0: number; y0: number; x1: number; y1: number }
export interface CssBox { left: number; top: number; width: number; height: number }
/** The geometry actually drawn for one object, in image pixels. */
export interface OverlayShape { polygons: number[][][]; box: CssBox | null }
export interface BadgeInput { displayNumber: number; text: string; bounds: Bounds }
export interface BadgePlacement extends Rect {
  displayNumber: number;
  text: string;
  leaderTarget: { x: number; y: number } | null;
}

export const BADGE_HEIGHT = 18;
const CHARACTER_WIDTH = 6.2; // 10px bold monospace
const CLEARANCE = 1.5;       // keeps a label visibly off a line, not just mathematically off it

export function badgeWidth(text: string): number {
  return Math.ceil(10 + text.length * CHARACTER_WIDTH);
}

/** Checks whose subject is a bounding box. */
export function isBoxCheck(checkName: string | null | undefined): boolean {
  return typeof checkName === 'string' && /(^|_)box(_|$)/.test(checkName);
}

export function drawsBox(
  obj: { bbox: unknown; polygons?: number[][][] | null; bbox_origin?: string | null },
  hasBoxFinding: boolean,
): boolean {
  if (obj.bbox == null) return false;
  if (!obj.polygons?.length) return true;
  return hasBoxFinding && obj.bbox_origin !== 'derived_from_polygon';
}

export function boundarySegments(shapes: readonly OverlayShape[]): Segment[] {
  const segments: Segment[] = [];
  for (const shape of shapes) {
    for (const part of shape.polygons) {
      for (let index = 0; index < part.length; index += 1) {
        const from = part[index]!;
        const to = part[(index + 1) % part.length]!;
        segments.push({ x0: from[0]!, y0: from[1]!, x1: to[0]!, y1: to[1]! });
      }
    }
    if (shape.box) {
      const { left, top, width, height } = shape.box;
      const right = left + width;
      const bottom = top + height;
      segments.push(
        { x0: left, y0: top, x1: right, y1: top }, { x0: right, y0: top, x1: right, y1: bottom },
        { x0: left, y0: bottom, x1: right, y1: bottom }, { x0: left, y0: top, x1: left, y1: bottom },
      );
    }
  }
  return segments;
}

/** True when any part of the segment lies inside the rectangle (Liang-Barsky clipping). */
export function segmentCrossesRect(segment: Segment, rect: Rect): boolean {
  const dx = segment.x1 - segment.x0;
  const dy = segment.y1 - segment.y0;
  let enter = 0;
  let leave = 1;
  const edges: Array<[number, number]> = [
    [-dx, segment.x0 - rect.x], [dx, rect.x + rect.width - segment.x0],
    [-dy, segment.y0 - rect.y], [dy, rect.y + rect.height - segment.y0],
  ];
  for (const [direction, distance] of edges) {
    if (direction === 0) {
      if (distance < 0) return false;
      continue;
    }
    const t = distance / direction;
    if (direction < 0) enter = Math.max(enter, t);
    else leave = Math.min(leave, t);
    if (enter > leave) return false;
  }
  return true;
}

function overlapArea(a: Rect, b: Rect): number {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > 0 && height > 0 ? width * height : 0;
}

const grown = (rect: Rect): Rect => ({
  x: rect.x - CLEARANCE, y: rect.y - CLEARANCE, width: rect.width + 2 * CLEARANCE, height: rect.height + 2 * CLEARANCE,
});

/** Place one label per object: off every outline, off every other label, inside the image. */
export function placeBadges(
  objects: readonly BadgeInput[], panelWidth: number, panelHeight: number, segments: readonly Segment[] = [],
): BadgePlacement[] {
  const occupied: Rect[] = [];
  const placements: BadgePlacement[] = [];
  // Smaller objects first: they have the fewest good places near them.
  const ordered = [...objects].sort((a, b) =>
    (a.bounds.x1 - a.bounds.x0) * (a.bounds.y1 - a.bounds.y0) - (b.bounds.x1 - b.bounds.x0) * (b.bounds.y1 - b.bounds.y0)
    || a.displayNumber - b.displayNumber);

  for (const { bounds, displayNumber, text } of ordered) {
    const width = badgeWidth(text);
    const height = BADGE_HEIGHT;
    const gap = CLEARANCE + 1;
    const centreX = (bounds.x0 + bounds.x1) / 2;
    const centreY = (bounds.y0 + bounds.y1) / 2;
    // Just outside the object first (above, below, left, right), then its middle, then further out in rings.
    const candidates: Array<[number, number]> = [
      [bounds.x0, bounds.y0 - height - gap], [bounds.x1 - width, bounds.y0 - height - gap],
      [centreX - width / 2, bounds.y0 - height - gap],
      [bounds.x0, bounds.y1 + gap], [bounds.x1 - width, bounds.y1 + gap], [centreX - width / 2, bounds.y1 + gap],
      [bounds.x0 - width - gap, bounds.y0], [bounds.x1 + gap, bounds.y0],
      [bounds.x0 - width - gap, centreY - height / 2], [bounds.x1 + gap, centreY - height / 2],
      [centreX - width / 2, centreY - height / 2],
    ];
    for (let ring = 1; ring <= 14; ring += 1) {
      const stepX = ring * (width * 0.5 + 2);
      const stepY = ring * (height * 0.75 + 2);
      for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, -1], [0, 1], [-1, 0], [1, 0],
        [-2, -1], [2, -1], [-2, 1], [2, 1], [-1, -2], [1, -2], [-1, 2], [1, 2]] as Array<[number, number]>) {
        candidates.push([centreX - width / 2 + dx * stepX, centreY - height / 2 + dy * stepY]);
      }
    }

    let best: Rect = { x: Math.max(0, Math.min(bounds.x0, panelWidth - width)), y: Math.max(0, bounds.y0 - height - gap), width, height };
    let bestCost = Infinity;
    for (const [left, top] of candidates) {
      const rect: Rect = {
        x: Math.min(Math.max(left, 0), Math.max(panelWidth - width, 0)),
        y: Math.min(Math.max(top, 0), Math.max(panelHeight - height, 0)),
        width, height,
      };
      const clear = grown(rect);
      let cost = 0;
      for (const other of occupied) cost += overlapArea(clear, other) * 4;
      for (const segment of segments) if (segmentCrossesRect(segment, clear)) cost += width * height;
      if (cost < bestCost) {
        bestCost = cost;
        best = rect;
        if (cost === 0) break;
      }
    }
    occupied.push(best);

    const touching = best.x <= bounds.x1 + 4 && best.x + best.width >= bounds.x0 - 4
      && best.y <= bounds.y1 + 4 && best.y + best.height >= bounds.y0 - 4;
    placements.push({
      displayNumber, text, ...best,
      leaderTarget: touching ? null : {
        x: Math.min(Math.max(best.x + best.width / 2, bounds.x0), bounds.x1),
        y: Math.min(Math.max(best.y + best.height / 2, bounds.y0), bounds.y1),
      },
    });
  }
  return placements;
}
