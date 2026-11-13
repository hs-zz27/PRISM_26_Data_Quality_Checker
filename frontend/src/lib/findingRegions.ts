/**
 * Where a measured person finding says the problem is, read from its evidence.
 * The record format is documented in backend/api/AUDITS.md ("Finding regions").
 */

export type RegionKind = 'missing' | 'extra' | 'mismatch' | 'unannotated';
export type CoordinateSystem = 'zero_based_continuous' | 'one_based_inclusive';
export type Box = [number, number, number, number];

export interface FindingRegion {
  kind: RegionKind;
  /** Which supplied geometry the region belongs to: box, polygon or mask. Null for a person with no annotation. */
  geometry: string | null;
  side: string | null;
  bodyPart: string | null;
  /** [x0, y0, x1, y1] in the annotation's own coordinates. */
  box: Box;
  /** How far a box side is off, or how tall an unannotated person is. */
  sizePx: number | null;
  areaPx: number | null;
  coordinateSystem: CoordinateSystem;
}

type Json = Record<string, unknown>;

const isRecord = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const list = (value: unknown): Json[] => (Array.isArray(value) ? value.filter(isRecord) : []);
const isBox = (value: unknown): value is Box =>
  Array.isArray(value) && value.length === 4 && value.every((v) => typeof v === 'number' && Number.isFinite(v));
const number = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const text = (value: unknown) => (typeof value === 'string' && value ? value : null);
const coordinates = (value: unknown): CoordinateSystem =>
  (value === 'one_based_inclusive' ? 'one_based_inclusive' : 'zero_based_continuous');

const COVERAGE_KINDS = new Set(['missing', 'extra', 'mismatch']);

/** Every region a finding reports, in the order the check recorded them. Unknown or malformed records are skipped. */
export function findingRegions(checkName: string, measurements: unknown): FindingRegion[] {
  const result = isRecord(measurements) && isRecord(measurements.result) ? measurements.result : null;
  if (!result) return [];
  if (checkName === 'person_coverage') {
    return list(result.geometries).flatMap((geometry) => list(geometry.regions).flatMap((region) => {
      const kind = text(region.kind);
      if (!kind || !COVERAGE_KINDS.has(kind) || !isBox(region.box)) return [];
      return [{ kind: kind as RegionKind, geometry: text(geometry.geometry), side: text(region.side),
        bodyPart: text(region.body_part), box: region.box, sizePx: number(region.pixels),
        areaPx: number(region.area_px), coordinateSystem: coordinates(geometry.coordinate_system) }];
    }));
  }
  if (checkName === 'unannotated_people') {
    return list(result.unannotated).flatMap((person) => (isBox(person.box)
      ? [{ kind: 'unannotated' as const, geometry: null, side: null, bodyPart: null, box: person.box,
        sizePx: number(person.height_px), areaPx: null, coordinateSystem: coordinates(result.coordinate_system) }]
      : []));
  }
  return [];
}

/** The region as image pixels from the top left at 0, ending after its last pixel: [left, top, right, bottom]. */
export function regionPixelBox(region: FindingRegion): Box {
  const [x0, y0, x1, y1] = region.box;
  return region.coordinateSystem === 'one_based_inclusive' ? [x0 - 1, y0 - 1, x1, y1] : [x0, y0, x1, y1];
}
