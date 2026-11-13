/**
 * Problems the reader found in an annotation file, in a form a reviewer can read.
 *
 * The application never repairs source geometry: a polygon that crosses itself or leaves the image is shown
 * exactly as the file has it. These rows are how that problem is made visible instead.
 */
export interface SourceDiagnosticRow {
  /** Stable annotation key, for example "/shapes/3"; null for a problem with the file or image as a whole. */
  key: string | null;
  /** The number drawn on the image for that annotation. Display only; the key identifies it. */
  displayNumber: number | null;
  label: string | null;
  /** Where in the file the problem is, as the reader reported it; empty for the file as a whole. */
  location: string;
  message: string;
  /** True when the content is kept but not understood (an unsupported shape, an embedded image). */
  unsupported: boolean;
}

interface KeyedObject { object_id: number; label: string; source_key?: string | null }

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

export function sourceDiagnostics(raw: unknown, objects: readonly KeyedObject[]): SourceDiagnosticRow[] {
  if (!Array.isArray(raw)) return [];
  const rows: SourceDiagnosticRow[] = [];
  for (const item of raw) {
    if (item == null || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    const message = text(record.message) ?? text(record.code);
    if (!message) continue;
    const location = text(record.location) ?? '';
    // The owner is the annotation whose key is the location or a parent of it ("/shapes/1" owns
    // "/shapes/1/points" but not "/shapes/10").
    const index = objects.findIndex((object) => object.source_key != null
      && (location === object.source_key || location.startsWith(`${object.source_key}/`)));
    const owner = index >= 0 ? objects[index] : undefined;
    rows.push({
      key: owner?.source_key ?? null,
      displayNumber: owner ? index + 1 : null,
      label: owner?.label ?? null,
      location,
      message,
      unsupported: record.severity === 'unsupported',
    });
  }
  return rows;
}
