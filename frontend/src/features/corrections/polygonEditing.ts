import type { JsonObject } from './jsonGeometry';

export type Point = [number, number];
export type Vertex = { key: string; part: number; point: number };

export function isClosed(part: number[][]): boolean {
  const first = part[0]; const last = part[part.length - 1];
  return part.length > 1 && !!first && !!last && first[0] === last[0] && first[1] === last[1];
}

/** Ignore an optional duplicate closing vertex when presenting editing handles. */
export function vertices(part: number[][]): number[][] {
  return isClosed(part) ? part.slice(0, -1) : part;
}

export function imagePoint(client: Point, rect: { left: number; top: number; width: number; height: number }, size: Point): Point | null {
  if (![...client, rect.left, rect.top, rect.width, rect.height, ...size].every(Number.isFinite)
    || rect.width <= 0 || rect.height <= 0 || size[0] <= 0 || size[1] <= 0) return null;
  return [Math.max(0, Math.min(size[0], (client[0] - rect.left) * size[0] / rect.width)),
    Math.max(0, Math.min(size[1], (client[1] - rect.top) * size[1] / rect.height))];
}

/** Edit one native polygon part; retain source identity and independent supplied boxes. */
function editPart(objects: JsonObject[], vertex: Vertex, edit: (points: Point[]) => Point[]): JsonObject[] {
  const index = objects.findIndex((object) => object.source_key === vertex.key);
  const object = objects[index]; const part = object?.polygons?.[vertex.part];
  if (!object || !part || !vertices(part)[vertex.point]) throw new Error('Select an existing polygon point.');
  const closed = isClosed(part);
  const points = edit(vertices(part).map((point) => [point[0]!, point[1]!]));
  if (points.length < 3 || new Set(points.map((point) => JSON.stringify(point))).size < 3) {
    throw new Error('A polygon needs at least three different points.');
  }
  if (points.some((point) => !point.every(Number.isFinite))) throw new Error('Coordinates must be finite numbers.');
  const changed = closed ? [...points, [...points[0]!] as Point] : points;
  const polygons = object.polygons!.map((old, i) => i === vertex.part ? changed : old);
  let bbox = object.bbox;
  if (object.bbox_origin === 'derived_from_polygon') {
    const all = polygons.flat();
    // Iteration avoids argument-count limits on dense multipart annotations.
    bbox = all.reduce<[number, number, number, number]>((bounds, point) => [
      Math.min(bounds[0], point[0]!), Math.min(bounds[1], point[1]!),
      Math.max(bounds[2], point[0]!), Math.max(bounds[3], point[1]!),
    ], [Infinity, Infinity, -Infinity, -Infinity]);
  }
  return objects.map((old, i) => i === index ? { ...object, polygons, bbox } : old);
}

export function moveVertex(objects: JsonObject[], vertex: Vertex, point: Point, size: Point): JsonObject[] {
  if (![...point, ...size].every(Number.isFinite) || size.some((value) => value <= 0)) throw new Error('Image coordinates must be finite and dimensions must be positive.');
  const bounded: Point = [Math.max(0, Math.min(size[0], point[0])), Math.max(0, Math.min(size[1], point[1]))];
  return editPart(objects, vertex, (points) => points.map((old, index) => index === vertex.point ? bounded : old));
}

export function insertVertex(objects: JsonObject[], vertex: Vertex): JsonObject[] {
  return editPart(objects, vertex, (points) => {
    const current = points[vertex.point]!; const next = points[(vertex.point + 1) % points.length]!;
    const midpoint: Point = [(current[0] + next[0]) / 2, (current[1] + next[1]) / 2];
    return [...points.slice(0, vertex.point + 1), midpoint, ...points.slice(vertex.point + 1)];
  });
}

export function removeVertex(objects: JsonObject[], vertex: Vertex): JsonObject[] {
  return editPart(objects, vertex, (points) => points.filter((_, index) => index !== vertex.point));
}
