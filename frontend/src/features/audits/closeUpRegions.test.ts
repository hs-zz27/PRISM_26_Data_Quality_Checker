import { describe, expect, it } from 'vitest';
import {
  closeUpRegions, closeUpWindow, edgeSentence, regionDetail, regionTitle, REGION_STYLES,
  MAX_OUTPUT_PX, MAX_SCALE, MIN_PADDING_PX, PADDING_FRACTION,
} from './closeUpRegions';
import type { Box, FindingRegion } from '../../lib/findingRegions';
import { makeFinding } from '../../test/fixtures';

const IMAGE: [number, number] = [640, 480];
const region = (overrides: Partial<FindingRegion> = {}): FindingRegion => ({
  kind: 'missing', geometry: 'polygon', side: 'top', bodyPart: null, box: [300, 200, 360, 240], sizePx: null,
  areaPx: 900, coordinateSystem: 'zero_based_continuous', ...overrides,
});

describe('closeUpWindow', () => {
  it('pads the region by 30 to 50% of its size and by at least 96 px on every side', () => {
    expect(PADDING_FRACTION).toBeGreaterThanOrEqual(0.3);
    expect(PADDING_FRACTION).toBeLessThanOrEqual(0.5);
    expect(MIN_PADDING_PX).toBe(96);
    // Small region: the 96 px minimum decides.
    expect(closeUpWindow([300, 200, 360, 240], IMAGE)).toMatchObject({ x0: 204, y0: 104, x1: 456, y1: 336 });
    // Large region in a large image: the fraction decides (0.4 x 1000 = 400, 0.4 x 500 = 200).
    expect(closeUpWindow([1000, 1000, 2000, 1500], [4000, 3000])).toMatchObject({ x0: 600, y0: 800, x1: 2400, y1: 1700 });
  });

  it('never cuts through the problem region and never leaves the image', () => {
    const boxes: Box[] = [];
    for (let seed = 1; seed <= 400; seed += 1) {
      const left = (seed * 37) % 600;
      const top = (seed * 53) % 440;
      boxes.push([left, top, Math.min(640, left + 1 + (seed * 17) % 300), Math.min(480, top + 1 + (seed * 29) % 250)]);
    }
    boxes.push([0, 0, 640, 480], [0, 0, 1, 1], [639, 479, 640, 480], [10.5, 20.25, 30.75, 40.5]);
    for (const box of boxes) {
      const window = closeUpWindow(box, IMAGE)!;
      expect(window.x0).toBeLessThanOrEqual(box[0]);
      expect(window.y0).toBeLessThanOrEqual(box[1]);
      expect(window.x1).toBeGreaterThanOrEqual(box[2]);
      expect(window.y1).toBeGreaterThanOrEqual(box[3]);
      expect(window.x0).toBeGreaterThanOrEqual(0);
      expect(window.y0).toBeGreaterThanOrEqual(0);
      expect(window.x1).toBeLessThanOrEqual(640);
      expect(window.y1).toBeLessThanOrEqual(480);
      expect([window.x0, window.y0, window.x1, window.y1].every(Number.isInteger)).toBe(true);
      // Away from the image border, the padding is never less than the rule allows.
      if (window.x0 > 0) expect(box[0] - window.x0).toBeGreaterThanOrEqual(MIN_PADDING_PX);
      if (window.y1 < 480) expect(window.y1 - box[3]).toBeGreaterThanOrEqual(MIN_PADDING_PX);
    }
  });

  it('keeps the output within the maximum size and never enlarges a region too far', () => {
    for (const [box, size] of [[[300, 200, 360, 240], IMAGE], [[1000, 1000, 2000, 1500], [4000, 3000]],
      [[2, 2, 4, 4], [20, 20]], [[0, 0, 640, 480], IMAGE]] as Array<[Box, [number, number]]>) {
      const window = closeUpWindow(box, size)!;
      expect(Math.max(window.outputWidth, window.outputHeight)).toBeLessThanOrEqual(MAX_OUTPUT_PX);
      expect(window.scale).toBeLessThanOrEqual(MAX_SCALE);
      expect(window.outputWidth).toBe(Math.max(1, Math.round((window.x1 - window.x0) * window.scale)));
      expect(window.outputHeight).toBe(Math.max(1, Math.round((window.y1 - window.y0) * window.scale)));
    }
    // A tiny image is enlarged, but only up to the limit.
    expect(closeUpWindow([2, 2, 4, 4], [20, 20])!.scale).toBe(MAX_SCALE);
  });

  it('shows a region at the image edge as being at the edge', () => {
    const top = closeUpWindow([300, 0, 360, 30], IMAGE)!;
    expect(top.y0).toBe(0);
    expect(top.imageEdges).toEqual(['top']);
    expect(top.regionEdges).toEqual(['top']);
    expect(edgeSentence(top)).toBe('The problem area touches the top edge of the photo.');

    const corner = closeUpWindow([600, 440, 640, 480], IMAGE)!;
    expect(corner.imageEdges).toEqual(['right', 'bottom']);
    expect(corner.regionEdges).toEqual(['right', 'bottom']);
    expect(edgeSentence(corner)).toBe('The problem area touches the right and bottom edges of the photo.');

    // Near an edge but not on it: the close-up reaches the photo's edge, the region does not.
    const near = closeUpWindow([20, 200, 60, 240], IMAGE)!;
    expect(near.imageEdges).toEqual(['left']);
    expect(near.regionEdges).toEqual([]);
    expect(edgeSentence(near)).toBe('The close-up reaches the left edge of the photo.');

    const inside = closeUpWindow([300, 200, 360, 240], IMAGE)!;
    expect(inside.imageEdges).toEqual([]);
    expect(edgeSentence(inside)).toBeNull();
  });

  it('clips a region that runs past the photo to the photo, and says so', () => {
    const window = closeUpWindow([-40, 100, 50, 520], IMAGE)!;
    expect(window.region).toEqual([0, 100, 50, 480]);
    expect(window.regionEdges).toEqual(['left', 'bottom']);
  });

  it('returns nothing for a region that cannot be shown', () => {
    expect(closeUpWindow([700, 500, 800, 600], IMAGE)).toBeNull();
    expect(closeUpWindow([100, 100, 100, 200], IMAGE)).toBeNull();
    expect(closeUpWindow([10, 10, 20, 20], [0, 0])).toBeNull();
    expect(closeUpWindow([Number.NaN, 0, 10, 10], IMAGE)).toBeNull();
  });
});

describe('words for a region, built from the region record', () => {
  it('names the kind, the place and the size without reading the finding text', () => {
    expect(regionTitle(region())).toBe('Missing part: head or top of the person');
    expect(regionTitle(region({ side: 'bottom' }))).toBe('Missing part: feet or bottom of the person');
    expect(regionTitle(region({ side: 'left', bodyPart: 'arm' }))).toBe('Missing part: arm');
    expect(regionTitle(region({ kind: 'extra', side: 'right' }))).toBe('Extra background: on the right');
    expect(regionTitle(region({ kind: 'extra', side: 'bottom' }))).toBe('Extra background: at the bottom');
    expect(regionTitle(region({ kind: 'extra', side: 'middle', bodyPart: 'arm' }))).toBe('Extra background: in the middle');
    expect(regionTitle(region({ kind: 'mismatch', side: 'middle' }))).toBe('Wrong shape: middle of the person');
    expect(regionTitle(region({ kind: 'unannotated', geometry: null, side: null }))).toBe('Person with no annotation');
    expect(regionTitle(region({ side: null }))).toBe('Missing part');

    expect(regionDetail(region())).toBe('Outline, about 900 px in area');
    expect(regionDetail(region({ geometry: 'box', sizePx: 29, areaPx: null }))).toBe('Box, off by 29 px');
    expect(regionDetail(region({ geometry: 'mask', areaPx: 12 }))).toBe('Mask, about 12 px in area');
    expect(regionDetail(region({ kind: 'unannotated', geometry: null, sizePx: 253, areaPx: null }))).toBe('About 253 px tall');
    expect(regionDetail(region({ areaPx: null }))).toBe('Outline');
  });

  it('gives each kind its own word, pattern and line style', () => {
    const styles = Object.values(REGION_STYLES);
    expect(Object.keys(REGION_STYLES).sort()).toEqual(['extra', 'mismatch', 'missing', 'unannotated']);
    for (const field of ['word', 'pattern', 'dash', 'color', 'meaning'] as const) {
      expect(new Set(styles.map((style) => style[field])).size).toBe(4);
    }
  });
});

describe('closeUpRegions', () => {
  const coverage = { result: { problems: ['text that must not be parsed'], geometries: [
    { geometry: 'polygon', coordinate_system: 'zero_based_continuous', regions: [
      { box: [300, 200, 360, 240], kind: 'missing', side: 'top', area_px: 900 },
      { box: [100, 300, 140, 420], kind: 'extra', side: 'left', area_px: 400 },
      { box: [900, 900, 950, 950], kind: 'extra', side: 'right', area_px: 50 }] }] } };

  it('lists the regions that can be shown, in the recorded order, with their close-up window', () => {
    const finding = makeFinding({ check_name: 'person_coverage', evidence: { measurements: coverage } as never });
    const regions = closeUpRegions(finding, IMAGE);
    expect(regions.map((item) => item.title)).toEqual(['Missing part: head or top of the person', 'Extra background: on the left']);
    expect(regions[0]!.window).toMatchObject({ x0: 204, y0: 104, x1: 456, y1: 336 });
    expect(regions[0]!.pixelBox).toEqual([300, 200, 360, 240]);
  });

  it('converts Penn-Fudan coordinates to image pixels', () => {
    const penn = { result: { geometries: [{ geometry: 'box', coordinate_system: 'one_based_inclusive', regions: [
      { box: [301, 201, 360, 240], kind: 'missing', side: 'top', pixels: 40, edge: 201 }] }] } };
    const finding = makeFinding({ check_name: 'person_coverage', evidence: { measurements: penn } as never });
    expect(closeUpRegions(finding, IMAGE)[0]!.pixelBox).toEqual([300, 200, 360, 240]);
  });

  it('has nothing to show for other checks, missing evidence or an unknown image size', () => {
    expect(closeUpRegions(makeFinding(), IMAGE)).toEqual([]);
    expect(closeUpRegions(makeFinding({ check_name: 'person_coverage', evidence: null as never }), IMAGE)).toEqual([]);
    const finding = makeFinding({ check_name: 'person_coverage', evidence: { measurements: coverage } as never });
    expect(closeUpRegions(finding, null)).toEqual([]);
    expect(closeUpRegions(finding, undefined)).toEqual([]);
  });

  it('reads a person with no annotation', () => {
    const measurements = { result: { coordinate_system: 'zero_based_continuous',
      unannotated: [{ box: [110, 84, 213, 337], height_px: 253, score: 0.99 }] } };
    const finding = makeFinding({ check_name: 'unannotated_people', object_id: null, evidence: { measurements } as never });
    const [only] = closeUpRegions(finding, IMAGE);
    expect(only!.title).toBe('Person with no annotation');
    expect(only!.detail).toBe('About 253 px tall');
  });
});
