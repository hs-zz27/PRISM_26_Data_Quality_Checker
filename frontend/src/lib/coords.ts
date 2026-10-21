/**
 * Coordinate System Utilities
 * ─────────────────────────────────────────────────────────────────────────
 * The backend uses ONE-BASED INCLUSIVE bounding boxes:
 *   [xmin, ymin, xmax, ymax] where origin is (1, 1)
 *   Both min and max coordinates are included in the box.
 *
 * CSS canvas rendering uses ZERO-BASED pixel coordinates where:
 *   left  = xmin - 1
 *   top   = ymin - 1
 *   width = xmax - xmin + 1      (includes both endpoints)
 *   height = ymax - ymin + 1
 *
 * This module is the ONLY place this conversion is computed.
 * Do not inline this math in any component.
 *
 * Reference: backend/api/FRONTEND_WORKFLOW.md §7 Assets and annotation correction
 * ─────────────────────────────────────────────────────────────────────────
 */

/** A bounding box in the backend's 1-based inclusive coordinate system. */
export type BBox1Based = [xmin: number, ymin: number, xmax: number, ymax: number];

/** A rendered box in CSS/canvas coordinate system (zero-based, pixel dimensions). */
export interface CSSBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Image size as returned by the API: [width, height] in pixels. */
export type ImageSize = [width: number, height: number];

/**
 * Convert a 1-based inclusive bounding box to CSS rendering coordinates.
 *
 * @param bbox - [xmin, ymin, xmax, ymax] one-based inclusive
 * @returns CSS box with zero-based left/top and pixel dimensions
 *
 * @example
 *   bboxToCss([1, 1, 10, 20])
 *   // => { left: 0, top: 0, width: 10, height: 20 }
 *
 *   bboxToCss([5, 3, 14, 22])
 *   // => { left: 4, top: 2, width: 10, height: 20 }
 */
export function bboxToCss(bbox: BBox1Based): CSSBox {
  const [xmin, ymin, xmax, ymax] = bbox;
  return {
    left: xmin - 1,
    top: ymin - 1,
    width: xmax - xmin + 1,
    height: ymax - ymin + 1,
  };
}

/**
 * Convert CSS rendering coordinates back to 1-based inclusive bounding box.
 *
 * @param box - CSS box with zero-based left/top and pixel dimensions
 * @returns [xmin, ymin, xmax, ymax] one-based inclusive
 *
 * @example
 *   cssToBbox({ left: 0, top: 0, width: 10, height: 20 })
 *   // => [1, 1, 10, 20]
 *
 *   cssToBbox({ left: 4, top: 2, width: 10, height: 20 })
 *   // => [5, 3, 14, 22]
 */
export function cssToBbox(box: CSSBox): BBox1Based {
  const xmin = box.left + 1;
  const ymin = box.top + 1;
  const xmax = box.left + box.width;       // left + 1 + (width - 1) = left + width
  const ymax = box.top + box.height;       // top  + 1 + (height - 1) = top + height
  return [xmin, ymin, xmax, ymax];
}

/** Move a 1-based inclusive box by a screen-pixel delta while keeping it in bounds. */
export function translateBboxOnCanvas(
  bbox: BBox1Based,
  deltaClientX: number,
  deltaClientY: number,
  displayedSize: ImageSize,
  imageSize: ImageSize,
): BBox1Based {
  const box = bboxToCss(bbox);
  const dx = Math.round((deltaClientX / displayedSize[0]) * imageSize[0]);
  const dy = Math.round((deltaClientY / displayedSize[1]) * imageSize[1]);
  const left = Math.min(imageSize[0] - box.width, Math.max(0, box.left + dx));
  const top = Math.min(imageSize[1] - box.height, Math.max(0, box.top + dy));
  return cssToBbox({ ...box, left, top });
}

/**
 * Scale a CSS box by a zoom factor (for canvas viewport rendering).
 *
 * @param box - CSS box
 * @param scale - zoom factor (1.0 = 100%)
 * @returns Scaled CSS box
 */
export function scaleCssBox(box: CSSBox, scale: number): CSSBox {
  return {
    left: box.left * scale,
    top: box.top * scale,
    width: box.width * scale,
    height: box.height * scale,
  };
}

/**
 * Check whether a 1-based inclusive bbox is valid for a given image size.
 * Validates:
 *   - All coordinates are positive integers
 *   - xmin <= xmax, ymin <= ymax
 *   - xmax <= imageWidth, ymax <= imageHeight
 *
 * @param bbox - [xmin, ymin, xmax, ymax] one-based inclusive
 * @param imageSize - [width, height] of the image
 * @returns true if valid
 */
export function isBboxValid(bbox: BBox1Based, imageSize: ImageSize): boolean {
  const [xmin, ymin, xmax, ymax] = bbox;
  const [w, h] = imageSize;

  if (!Number.isInteger(xmin) || !Number.isInteger(ymin) ||
      !Number.isInteger(xmax) || !Number.isInteger(ymax)) {
    return false;
  }

  if (xmin < 1 || ymin < 1) return false;
  if (xmin > xmax || ymin > ymax) return false;
  if (xmax > w || ymax > h) return false;

  return true;
}

/**
 * Format a 1-based bbox as a human-readable string for display.
 * Uses tabular monospace format.
 *
 * @example
 *   formatBbox([160, 182, 302, 431])
 *   // => "[160, 182, 302, 431]"
 */
export function formatBbox(bbox: BBox1Based): string {
  return `[${bbox.join(', ')}]`;
}

/**
 * Format a 1-based point as a cursor position display string.
 *
 * @param x - 1-based x coordinate (pixel under cursor, 1-indexed)
 * @param y - 1-based y coordinate
 */
export function formatCursorPos(x: number, y: number): string {
  return `${x}, ${y}`;
}

/**
 * Convert a CSS pixel position (zero-based) to a 1-based coordinate
 * for display under the canvas cursor.
 *
 * @param cssX - zero-based x pixel position
 * @param cssY - zero-based y pixel position
 * @param scale - current canvas zoom scale (1.0 = 100%)
 * @returns [x, y] in 1-based image coordinates
 */
export function cssToImageCoord(
  cssX: number,
  cssY: number,
  scale: number,
): [x: number, y: number] {
  return [Math.floor(cssX / scale) + 1, Math.floor(cssY / scale) + 1];
}
