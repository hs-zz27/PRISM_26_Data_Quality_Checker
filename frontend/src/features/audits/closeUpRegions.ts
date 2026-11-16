/**
 * The error close-up: which part of the photo to show for one problem region, and the words for it.
 *
 * Everything here comes from the region records read by findingRegions(), never from the finding text. The
 * crop follows the window logic of backend/app/services/target_focus.py: pad the region, clip to the photo,
 * scale to a bounded size. The close-up is drawn in the browser over the original photo; nothing is saved.
 */
import { findingRegions, regionPixelBox, type Box, type FindingRegion, type RegionKind } from '../../lib/findingRegions';
import type { components } from '../../api/generated/openapi';

type Finding = components['schemas']['Finding'];
export type Side = 'left' | 'top' | 'right' | 'bottom';

/** Padding on every side: this share of the region's size, and never less than MIN_PADDING_PX. */
export const PADDING_FRACTION = 0.4;
export const MIN_PADDING_PX = 96;
/** The longer side of the close-up is at most this many screen pixels. */
export const MAX_OUTPUT_PX = 420;
/** A small region is enlarged at most this much. */
export const MAX_SCALE = 4;

export interface CloseUpWindow {
  /** The part of the photo shown, in image pixels from the top left at 0. */
  x0: number; y0: number; x1: number; y1: number;
  scale: number;
  outputWidth: number;
  outputHeight: number;
  /** The problem region, clipped to the photo. Always inside the window. */
  region: Box;
  /** Window sides that are the photo's own border. */
  imageEdges: Side[];
  /** Region sides that lie on the photo's border. */
  regionEdges: Side[];
}

export function closeUpWindow(regionBox: Box, imageSize: readonly [number, number] | null | undefined): CloseUpWindow | null {
  if (!imageSize || !regionBox.every(Number.isFinite)) return null;
  const [width, height] = imageSize;
  if (!(width > 0) || !(height > 0)) return null;
  const left = Math.max(0, regionBox[0]);
  const top = Math.max(0, regionBox[1]);
  const right = Math.min(width, regionBox[2]);
  const bottom = Math.min(height, regionBox[3]);
  if (right <= left || bottom <= top) return null;
  const padX = Math.max(PADDING_FRACTION * (right - left), MIN_PADDING_PX);
  const padY = Math.max(PADDING_FRACTION * (bottom - top), MIN_PADDING_PX);
  const x0 = Math.max(0, Math.floor(left - padX));
  const y0 = Math.max(0, Math.floor(top - padY));
  const x1 = Math.min(width, Math.ceil(right + padX));
  const y1 = Math.min(height, Math.ceil(bottom + padY));
  const scale = Math.min(MAX_SCALE, MAX_OUTPUT_PX / Math.max(x1 - x0, y1 - y0));
  const sides = (hits: [Side, boolean][]) => hits.filter(([, hit]) => hit).map(([side]) => side);
  return {
    x0, y0, x1, y1, scale,
    outputWidth: Math.max(1, Math.round((x1 - x0) * scale)),
    outputHeight: Math.max(1, Math.round((y1 - y0) * scale)),
    region: [left, top, right, bottom],
    imageEdges: sides([['left', x0 === 0], ['top', y0 === 0], ['right', x1 === width], ['bottom', y1 === height]]),
    regionEdges: sides([['left', left <= 0], ['top', top <= 0], ['right', right >= width], ['bottom', bottom >= height]]),
  };
}

function joined(sides: Side[]): string {
  return sides.length > 1 ? `${sides.slice(0, -1).join(', ')} and ${sides[sides.length - 1]} edges` : `${sides[0]} edge`;
}

/** Says when the problem area, or only the close-up, is at the border of the photo. Null when neither is. */
export function edgeSentence(window: CloseUpWindow): string | null {
  if (window.regionEdges.length) return `The problem area touches the ${joined(window.regionEdges)} of the photo.`;
  if (window.imageEdges.length) return `The close-up reaches the ${joined(window.imageEdges)} of the photo.`;
  return null;
}

/**
 * One look per kind of problem. The kinds follow the mentor's rule for a tight annotation: it follows the
 * person's visible boundary with no missing visible part, no extra background and no gap; small deviations are
 * accepted and only noticeable ones are reported.
 * Each has its own word, fill pattern and line style as well as its own colour,
 * so the kind can be told without seeing colour.
 */
export const REGION_STYLES: Record<RegionKind, { word: string; meaning: string; pattern: string; dash: string; color: string }> = {
  missing: { word: 'MISSING', meaning: 'A visible part of the person is outside the annotation', pattern: 'diagonal', dash: 'none', color: '#ff5a5f' },
  extra: { word: 'EXTRA', meaning: 'The annotation takes in background that is not the person', pattern: 'dots', dash: '6 4', color: '#ffb000' },
  mismatch: { word: 'WRONG SHAPE', meaning: 'The outline does not follow the person’s visible boundary', pattern: 'cross', dash: '2 3', color: '#c77dff' },
  unannotated: { word: 'NO ANNOTATION', meaning: 'A visible person has no annotation', pattern: 'horizontal', dash: '10 3 2 3', color: '#4cc9f0' },
};

const PLACE: Record<string, string> = {
  top: 'head or top of the person', bottom: 'feet or bottom of the person', left: 'left side', right: 'right side',
  middle: 'middle of the person',
};
const WHERE: Record<string, string> = {
  top: 'at the top', bottom: 'at the bottom', left: 'on the left', right: 'on the right', middle: 'in the middle',
};
const KIND_NAME: Record<RegionKind, string> = {
  missing: 'Missing part', extra: 'Extra background', mismatch: 'Wrong shape', unannotated: 'Person with no annotation',
};
const GEOMETRY_NAME: Record<string, string> = { box: 'Box', polygon: 'Outline', mask: 'Mask' };

export function regionTitle(region: FindingRegion): string {
  const name = KIND_NAME[region.kind];
  if (region.kind === 'unannotated') return name;
  // Extra area is described by where it is; a missing or misdrawn part by which part of the person it is.
  const places = region.kind === 'extra' ? WHERE : PLACE;
  const place = (region.kind === 'extra' ? null : region.bodyPart) ?? (region.side ? places[region.side] ?? region.side : null);
  return place ? `${name}: ${place}` : name;
}

export function regionDetail(region: FindingRegion): string {
  if (region.kind === 'unannotated') return region.sizePx != null ? `About ${Math.round(region.sizePx)} px tall` : '';
  const geometry = region.geometry ? GEOMETRY_NAME[region.geometry] ?? region.geometry : '';
  const size = region.sizePx != null ? `off by ${Math.round(region.sizePx)} px`
    : region.areaPx != null ? `about ${Math.round(region.areaPx)} px in area` : '';
  return [geometry, size].filter(Boolean).join(', ');
}

export interface CloseUpRegion {
  region: FindingRegion;
  /** The region in image pixels: [left, top, right, bottom]. */
  pixelBox: Box;
  window: CloseUpWindow;
  title: string;
  detail: string;
}

/** The regions of a finding that can be shown, in the order the check recorded them. */
export function closeUpRegions(
  finding: Pick<Finding, 'check_name' | 'evidence'>, imageSize: readonly [number, number] | null | undefined,
): CloseUpRegion[] {
  if (!finding.check_name) return [];
  return findingRegions(finding.check_name, finding.evidence?.measurements).flatMap((region) => {
    const pixelBox = regionPixelBox(region);
    const window = closeUpWindow(pixelBox, imageSize);
    return window ? [{ region, pixelBox, window, title: regionTitle(region), detail: regionDetail(region) }] : [];
  });
}
