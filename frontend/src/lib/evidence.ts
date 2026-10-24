/**
 * Turn a finding's measured evidence into readable rows.
 * Measurement checks nest their numbers under `result` next to the annotated
 * object; known checks get plain-language rows and anything else is flattened
 * one level. FindingCard also exposes complete recorded evidence for inspection.
 */

import { formatValue, humanizeCode, reasonLabel } from './labels';

export interface EvidenceRow { label: string; value: string }

type Json = Record<string, unknown>;

const isRecord = (value: unknown): value is Json =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Envelope bookkeeping that carries no evidence for a reviewer. */
const NOISE = new Set(['status', 'detail', 'reason', 'source_index', 'pair_index', 'measurement_available', 'object_id', 'object_ids']);

const px = (value: unknown) => (typeof value === 'number' ? `${Math.abs(value)} px` : formatValue(value));
const percent = (value: unknown) => (typeof value === 'number' ? `${Math.round(value * 1000) / 10}%` : formatValue(value));
const ids = (value: unknown) => (Array.isArray(value) ? (value.length ? value.map((id) => `#${formatValue(id)}`).join(', ') : 'none') : formatValue(value));
const box = (value: unknown) => (Array.isArray(value) && value.length === 4 ? `[${value.join(', ')}]` : formatValue(value));

function knownRows(checkName: string, result: Json, measurements: Json): EvidenceRow[] | null {
  switch (checkName) {
    case 'box_mask_alignment': {
      const edges = isRecord(result.edge_differences) ? result.edge_differences : {};
      return [
        { label: 'Largest edge gap', value: px(result.max_edge_difference) },
        { label: 'Edge gaps', value: ['left', 'right', 'top', 'bottom'].map((side) => `${side} ${px(edges[side])}`).join(', ') },
        { label: 'Annotated box', value: box(result.annotation_bbox) },
        { label: 'Person outline (mask)', value: box(result.mask_bbox) },
      ];
    }
    case 'mask_outside_box':
      return [
        { label: 'Mask outside box', value: `${percent(result.outside_fraction)} (${formatValue(result.outside_pixels)} of ${formatValue(result.total_mask_pixels)} pixels)` },
        { label: 'Annotated box', value: box(result.annotation_bbox) },
      ];
    case 'mask_box_fill':
      return [
        { label: 'Mask fills box', value: `${percent(result.fill_fraction)} (${formatValue(result.inside_mask_pixels)} of ${formatValue(result.box_area)} pixels)` },
        { label: 'Annotated box', value: box(result.annotation_bbox) },
      ];
    case 'mask_ids': {
      const rows = [
        { label: 'Mask regions', value: ids(result.mask_ids) },
        { label: 'Annotated objects', value: ids(result.annotation_ids) },
      ];
      if (Array.isArray(result.missing_annotations) && result.missing_annotations.length) {
        rows.push({ label: 'Mask regions with no box', value: ids(result.missing_annotations) });
      }
      if (Array.isArray(result.missing_masks) && result.missing_masks.length) {
        rows.push({ label: 'Boxes with no mask region', value: ids(result.missing_masks) });
      }
      return rows;
    }
    case 'box_validity': {
      const rows = [{ label: 'Box', value: box(result.bbox) }];
      if (typeof result.reason === 'string') rows.push({ label: 'Problem', value: reasonLabel(result.reason) });
      return rows;
    }
    case 'box_overlaps': {
      const rows = [
        { label: 'Objects compared', value: ids(result.object_ids) },
        { label: 'Overlap (IoU)', value: typeof result.iou === 'number' ? formatValue(result.iou) : 'not measured' },
      ];
      if (typeof result.reason === 'string') rows.push({ label: 'Why not measured', value: reasonLabel(result.reason) });
      return rows;
    }
    case 'allowed_labels':
      return [
        { label: 'Label', value: formatValue(measurements.actual_label) },
        { label: 'Allowed labels', value: formatValue(measurements.allowed_labels) },
      ];
    case 'annotation_metadata': {
      const declared = isRecord(result.declared) ? result.declared : {};
      const rows = [
        { label: 'Image size (declared / actual)', value: `${formatValue(declared.image_size)} / ${formatValue(result.actual_image_size)}` },
        { label: 'Objects (declared / actual)', value: `${formatValue(declared.object_count)} / ${formatValue(result.actual_object_count)}` },
      ];
      if (isRecord(result.issues) && Object.keys(result.issues).length) {
        rows.push({ label: 'Issues', value: Object.entries(result.issues).map(([k, v]) => `${humanizeCode(k)}: ${formatValue(v)}`).join('; ') });
      }
      return rows;
    }
    default:
      return null;
  }
}

/** Flatten one level of nesting, skipping envelope bookkeeping and empty values. */
function genericRows(measurements: Json): EvidenceRow[] {
  const rows: EvidenceRow[] = [];
  const add = (label: string, value: unknown) => {
    if (value === null || value === undefined || NOISE.has(label)) return;
    if (isRecord(value)) {
      for (const [childKey, childValue] of Object.entries(value)) {
        if (childValue === null || childValue === undefined || NOISE.has(childKey)) continue;
        rows.push({ label: `${humanizeCode(label)} · ${childKey.replace(/_/g, ' ')}`, value: formatValue(childValue) });
      }
      return;
    }
    rows.push({ label: humanizeCode(label), value: formatValue(value) });
  };
  for (const [key, value] of Object.entries(measurements)) add(key, value);
  return rows;
}

export function summarizeEvidence(checkName: string | null | undefined, measurements: Json | null | undefined): EvidenceRow[] {
  if (!measurements) return [];
  const result = isRecord(measurements.result) ? measurements.result : null;
  // Keep the concrete measurement failure visible even if the finding uses a generic reason.
  if (!result && measurements.status === 'unavailable') {
    return typeof measurements.reason === 'string'
      ? [{ label: 'Why not measured', value: reasonLabel(measurements.reason) }] : [];
  }
  const known = checkName ? knownRows(checkName, result ?? {}, measurements) : null;
  if (known && (result || checkName === 'allowed_labels')) return known;
  return genericRows(measurements);
}
