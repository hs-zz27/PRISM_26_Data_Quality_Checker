/**
 * Coordinate Conversion — Unit Tests
 * ─────────────────────────────────────────────────────────────────────────
 * Tests the only file that contains bounding-box coordinate math.
 * Every conversion function must be verified here.
 * ─────────────────────────────────────────────────────────────────────────
 */

import { describe, it, expect } from 'vitest';
import {
  resizeBboxOnCanvas,
  defaultNewBbox,
  bboxToCss,
  cssToBbox,
  isBboxValid,
  formatBbox,
  formatCursorPos,
  cssToImageCoord,
  translateBboxOnCanvas,
  type BBox1Based,
} from './coords';

describe('bboxToCss — 1-based inclusive → CSS zero-based', () => {
  it('converts origin box [1,1,10,20] to { left:0, top:0, width:10, height:20 }', () => {
    expect(bboxToCss([1, 1, 10, 20])).toEqual({ left: 0, top: 0, width: 10, height: 20 });
  });

  it('converts interior box [5,3,14,22]', () => {
    expect(bboxToCss([5, 3, 14, 22])).toEqual({ left: 4, top: 2, width: 10, height: 20 });
  });

  it('handles single-pixel box [5,5,5,5]', () => {
    expect(bboxToCss([5, 5, 5, 5])).toEqual({ left: 4, top: 4, width: 1, height: 1 });
  });

  it('matches Penn-Fudan example: [160, 182, 302, 431]', () => {
    // xmin=160, ymin=182, xmax=302, ymax=431
    // left=159, top=181, width=143, height=250
    expect(bboxToCss([160, 182, 302, 431])).toEqual({
      left: 159,
      top: 181,
      width: 143,
      height: 250,
    });
  });
});

describe('cssToBbox — CSS zero-based → 1-based inclusive', () => {
  it('converts { left:0, top:0, width:10, height:20 } to [1,1,10,20]', () => {
    expect(cssToBbox({ left: 0, top: 0, width: 10, height: 20 })).toEqual([1, 1, 10, 20]);
  });

  it('converts { left:4, top:2, width:10, height:20 } to [5,3,14,22]', () => {
    expect(cssToBbox({ left: 4, top: 2, width: 10, height: 20 })).toEqual([5, 3, 14, 22]);
  });

  it('converts single-pixel { left:4, top:4, width:1, height:1 } to [5,5,5,5]', () => {
    expect(cssToBbox({ left: 4, top: 4, width: 1, height: 1 })).toEqual([5, 5, 5, 5]);
  });
});

describe('round-trip bboxToCss → cssToBbox', () => {
  const testCases: BBox1Based[] = [
    [1, 1, 1, 1],
    [1, 1, 640, 480],
    [160, 182, 302, 431],
    [10, 20, 100, 200],
    [500, 300, 600, 400],
  ];

  for (const bbox of testCases) {
    it(`round-trips ${JSON.stringify(bbox)}`, () => {
      const css = bboxToCss(bbox);
      const back = cssToBbox(css);
      expect(back).toEqual(bbox);
    });
  }
});

describe('isBboxValid', () => {
  it('accepts valid bbox within image', () => {
    expect(isBboxValid([1, 1, 640, 480], [640, 480])).toBe(true);
  });

  it('accepts tight single pixel at origin', () => {
    expect(isBboxValid([1, 1, 1, 1], [100, 100])).toBe(true);
  });

  it('rejects xmin = 0 (not 1-based)', () => {
    expect(isBboxValid([0, 1, 10, 20], [100, 100])).toBe(false);
  });

  it('rejects ymin < 1', () => {
    expect(isBboxValid([1, 0, 10, 20], [100, 100])).toBe(false);
  });

  it('rejects xmin > xmax', () => {
    expect(isBboxValid([10, 1, 5, 20], [100, 100])).toBe(false);
  });

  it('rejects ymin > ymax', () => {
    expect(isBboxValid([1, 20, 10, 5], [100, 100])).toBe(false);
  });

  it('rejects xmax > image width', () => {
    expect(isBboxValid([1, 1, 641, 480], [640, 480])).toBe(false);
  });

  it('rejects ymax > image height', () => {
    expect(isBboxValid([1, 1, 640, 481], [640, 480])).toBe(false);
  });

  it('rejects non-integer coordinates', () => {
    expect(isBboxValid([1.5, 1, 10, 20], [100, 100])).toBe(false);
  });
});

describe('formatBbox', () => {
  it('formats Penn-Fudan example correctly', () => {
    expect(formatBbox([160, 182, 302, 431])).toBe('[160, 182, 302, 431]');
  });

  it('formats origin box', () => {
    expect(formatBbox([1, 1, 10, 20])).toBe('[1, 1, 10, 20]');
  });
});

describe('formatCursorPos', () => {
  it('formats cursor position', () => {
    expect(formatCursorPos(42, 137)).toBe('42, 137');
  });
});

describe('cssToImageCoord', () => {
  it('converts CSS pixel position to 1-based at 100% zoom', () => {
    // CSS pixel (0, 0) → image coordinate (1, 1)
    expect(cssToImageCoord(0, 0, 1)).toEqual([1, 1]);
  });

  it('converts CSS pixel (9, 19) to 1-based (10, 20) at 100% zoom', () => {
    expect(cssToImageCoord(9, 19, 1)).toEqual([10, 20]);
  });

  it('handles 2x zoom: CSS (20, 40) → image (11, 21)', () => {
    // floor(20/2) + 1 = 11, floor(40/2) + 1 = 21
    expect(cssToImageCoord(20, 40, 2)).toEqual([11, 21]);
  });

  it('handles 0.5x zoom: CSS (20, 40) → image (41, 81)', () => {
    // floor(20/0.5) + 1 = floor(40) + 1 = 41
    expect(cssToImageCoord(20, 40, 0.5)).toEqual([41, 81]);
  });
});

describe('translateBboxOnCanvas', () => {
  it('moves in image coordinates and clamps to image bounds', () => {
    expect(translateBboxOnCanvas([10, 10, 20, 20], 10, 0, [100, 100], [100, 100])).toEqual([20, 10, 30, 20]);
    expect(translateBboxOnCanvas([90, 90, 100, 100], 20, 20, [100, 100], [100, 100])).toEqual([90, 90, 100, 100]);
  });
});

describe('resizeBboxOnCanvas', () => {
  it('moves only the dragged corner, scaled from screen to image pixels', () => {
    // Displayed at half size, so 5 screen px = 10 image px.
    expect(resizeBboxOnCanvas([10, 10, 50, 50], 'se', 5, 5, [50, 50], [100, 100])).toEqual([10, 10, 60, 60]);
    expect(resizeBboxOnCanvas([10, 10, 50, 50], 'nw', -5, -5, [50, 50], [100, 100])).toEqual([1, 1, 50, 50]);
    expect(resizeBboxOnCanvas([10, 10, 50, 50], 'ne', 0, 10, [100, 100], [100, 100])).toEqual([10, 20, 50, 50]);
  });

  it('never inverts the box or leaves the image', () => {
    expect(resizeBboxOnCanvas([10, 10, 50, 50], 'nw', 200, 200, [100, 100], [100, 100])).toEqual([50, 50, 50, 50]);
    expect(resizeBboxOnCanvas([10, 10, 50, 50], 'se', 500, 500, [100, 100], [100, 100])).toEqual([10, 10, 100, 100]);
  });
});

describe('defaultNewBbox', () => {
  it('centres a valid box in the image', () => {
    expect(defaultNewBbox([200, 100])).toEqual([50, 25, 150, 75]);
    expect(isBboxValid(defaultNewBbox([3, 3]), [3, 3])).toBe(true);
    expect(defaultNewBbox(null)).toEqual([1, 1, 50, 50]);
  });
});
