import type { components } from '../../api/generated/openapi';
export type JsonObject = components['schemas']['JsonCorrectionObject'];

/** Read editor geometry without rounding source coordinates or inventing boxes. */
export function parseJsonGeometry(text: string): JsonObject[] {
  const value: unknown = JSON.parse(text);
  const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  if (!Array.isArray(value)) throw new Error('Enter an array of annotation objects.');
  return value.map((obj: unknown) => {
    if (typeof obj !== 'object' || obj === null) throw new Error('Each annotation must be an object.');
    const row = obj as Record<string, unknown>;
    if (row.coordinate_system !== 'zero_based_continuous' || !Number.isInteger(row.object_id)
      || typeof row.source_key !== 'string' || typeof row.label !== 'string') {
      throw new Error('Keep each source identity and provide a text label.');
    }
    if (row.bbox !== null && !(Array.isArray(row.bbox) && row.bbox.length === 4 && row.bbox.every(number))) {
      throw new Error('A box must be null or four finite numbers.');
    }
    if (!Array.isArray(row.polygons) || !row.polygons.every((part: unknown) => Array.isArray(part)
      && part.every((point: unknown) => Array.isArray(point) && point.length === 2 && point.every(number)))) {
      throw new Error('Polygons must contain arrays of [x, y] points.');
    }
    const result = { ...row } as unknown as JsonObject;
    if (result.bbox_origin === 'derived_from_polygon') {
      const points = result.polygons?.flat() ?? [];
      result.bbox = points.length ? [Math.min(...points.map((p) => p[0])), Math.min(...points.map((p) => p[1])),
        Math.max(...points.map((p) => p[0])), Math.max(...points.map((p) => p[1]))] : null;
    }
    return result;
  });
}
