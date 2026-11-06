/**
 * Plain-language labels for backend codes.
 * Keep raw codes out of the UI so non-technical reviewers can read every screen.
 * Unknown codes fall back to a readable form of the code itself.
 */

import type { components } from '../api/generated/openapi';

type Finding = components['schemas']['Finding'];

/** Turn `snake_case_code` into "Snake case code". */
export function humanizeCode(code: string | null | undefined): string {
  if (!code) return '—';
  const words = code.replace(/[_-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const TOOL_STATUS: Record<string, string> = {
  pending: 'Not checked yet',
  flagged: 'Flagged',
  unsure: 'Unsure',
  no_issue: 'No issue found',
  unavailable: 'Could not check',
};

const REVIEW_STATUS: Record<string, string> = {
  pending_review: 'Awaiting review',
  accepted: 'Accepted',
  rejected: 'Rejected',
  needs_correction: 'Needs correction',
};

const OUTCOME: Record<string, string> = {
  pass: 'Pass',
  fail: 'Fail',
  unsure: 'Unsure',
  unavailable: 'Could not check',
};

const RESOLUTION: Record<string, string> = {
  pending: 'Not reviewed',
  accepted: 'Finding confirmed',
  rejected: 'Finding overridden',
  unsure: 'Marked unsure',
};

const JOB_STATUS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  cancelling: 'Cancelling',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const CHECK_NAMES: Record<string, string> = {
  annotation_guideline: 'Visual guideline (AI-assisted)',
  pedestrian_box_enclosure: 'Pedestrian inside each annotation box (AI-assisted)',
  allowed_labels: 'Approved label list',
  annotation_ids: 'Unique object IDs',
  annotation_metadata: 'Annotation metadata matches image',
  box_validity: 'Box inside image',
  box_overlaps: 'Box overlap limit',
  box_mask_alignment: 'Box fits mask edges',
  mask_outside_box: 'Mask stays inside box',
  mask_box_fill: 'Mask fills box',
  mask_components: 'Mask is one connected piece',
  mask_dimensions: 'Mask size matches image',
  mask_ids: 'Mask IDs match objects',
  person_coverage: 'Whole person covered (measured)',
};

/** Reason codes emitted by checks and the audit worker. */
const REASONS: Record<string, string> = {
  no_person_detected: 'No person was found in the photo for this annotation — check it by eye',
  no_matching_person: 'No detected person matches this annotation — check it by eye',
  person_segmenter_unavailable: 'The person-outline model is not installed or could not run',
  no_supplied_geometry: 'The annotation has no box, mask or polygon to compare',
  label_not_allowed: 'Label is not in the approved list',
  invalid_label: 'Label is empty or invalid',
  invalid_box: 'Box coordinates are invalid or outside the image',
  box_outside_image: 'Box lies partly outside the image',
  evidence_unavailable: 'The measurement needed for this rule is missing',
  model_execution_unavailable: 'The AI check is switched off or unavailable',
  invalid_boxes: 'One or more boxes are invalid',
  invalid_object_id: 'Object ID is invalid',
  invalid_object_ids: 'Object IDs are invalid',
  duplicate_object_id: 'Object ID is used more than once',
  duplicate_ids: 'Object IDs are duplicated',
  invalid_ids: 'IDs are invalid',
  object_count_mismatch: 'Number of objects does not match the mask',
  missing_mask: 'Object has no mask region',
  missing_masks: 'Some objects have no mask region',
  missing_annotations: 'Mask regions have no matching annotation',
  dimensions_mismatch: 'Mask and image sizes differ',
  image_size_mismatch: 'Declared image size differs from the actual image',
  invalid_mask: 'Mask file could not be interpreted',
  mask_load_failed: 'Mask file could not be loaded',
  image_load_failed: 'Image file could not be loaded',
  annotation_load_failed: 'Annotation file could not be loaded',
  annotation_parse_failed: 'Annotation file could not be parsed',
  coordinate_origin_unavailable: 'Coordinate convention is unknown',
  unsupported_coordinate_origin: 'Coordinate convention is not supported',
  metadata_comparison_unavailable: 'Metadata could not be compared',
  dependent_check_execution_failed: 'An earlier check this depends on did not run',
  check_execution_failed: 'The check failed to run',
  measurement_available: 'Measurement available',
  source_data_unavailable: 'Source data is unavailable',
  source_unavailable: 'Source data is unavailable',
  object_coverage_mismatch: 'The model omitted an annotation or referred to an unknown object',
  unsupported_visual_geometry: 'The model judged geometry that was not supplied in its visual evidence',
  review_region_out_of_bounds: 'The model located an issue outside the image',
  no_annotation_objects_to_assess: 'No existing annotation objects are available for this visual check',
  retry_exhausted: 'Processing gave up after retries',
  processing_timeout: 'Processing timed out',
  unknown_check: 'No automatic check exists for this rule',
  unbound_rule: 'Rule has no automatic check — review manually',
};

const EXCLUSION_REASONS: Record<string, string> = {
  pending_review: 'Not reviewed yet',
  needs_correction: 'Marked as needing correction',
  rejected: 'Rejected by reviewer',
  annotation_text_missing: 'Annotation file missing',
  annotation_text_unreadable: 'Annotation file unreadable',
  raw_mask_missing: 'Mask file missing',
  raw_mask_unreadable: 'Mask file unreadable',
  original_image_missing: 'Image missing',
  original_image_unreadable: 'Image unreadable',
};

const lookup = (table: Record<string, string>) => (code: string | null | undefined) =>
  code ? table[code] ?? humanizeCode(code) : '—';

export const toolStatusLabel = lookup(TOOL_STATUS);
export const reviewStatusLabel = lookup(REVIEW_STATUS);
export const outcomeLabel = lookup(OUTCOME);
export const resolutionLabel = lookup(RESOLUTION);
export const jobStatusLabel = lookup(JOB_STATUS);
export const checkLabel = lookup(CHECK_NAMES);
export const reasonLabel = lookup(REASONS);
export const exclusionReasonLabel = lookup(EXCLUSION_REASONS);

/** Format a measurement or parameter value compactly for display. */
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return String(value);
    return Math.abs(value) < 1 ? value.toFixed(3) : value.toFixed(2);
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    return value.every((item) => typeof item !== 'object' || item === null)
      ? value.map(formatValue).join(', ')
      : JSON.stringify(value);
  }
  return JSON.stringify(value);
}

/** Readable requirement text for a check's configured parameters (e.g. "IoU ≤ 0.30"). */
export function describeRequirement(checkName: string | null | undefined, parameters: Record<string, unknown> | undefined): string | null {
  const p = parameters ?? {};
  const num = (key: string) => (typeof p[key] === 'number' ? formatValue(p[key]) : null);
  switch (checkName) {
    case 'allowed_labels':
      return Array.isArray(p.allowed_labels) ? `Label must be one of: ${formatValue(p.allowed_labels)}` : null;
    case 'box_overlaps':
      return num('max_iou') ? `Box overlap (IoU) ≤ ${num('max_iou')}` : null;
    case 'box_mask_alignment':
      return num('max_edge_difference') ? `Box edges within ${num('max_edge_difference')} px of mask` : null;
    case 'mask_outside_box':
      return num('max_outside_fraction') ? `Mask outside box ≤ ${percent(p.max_outside_fraction)}` : null;
    case 'mask_box_fill':
      return num('min_fill_fraction') && num('max_fill_fraction')
        ? `Mask fills ${percent(p.min_fill_fraction)}–${percent(p.max_fill_fraction)} of box`
        : null;
    case 'mask_components':
      return num('max_component_count') ? `At most ${num('max_component_count')} connected mask piece(s)` : null;
    case 'person_coverage':
      return `Whole person covered, within ${formatValue(p.min_tolerance_px ?? 8)} px or ${percent(p.tolerance_fraction ?? 0.04)} of the person's height`;
    default: {
      const entries = Object.entries(p);
      return entries.length ? entries.map(([k, v]) => `${humanizeCode(k)}: ${formatValue(v)}`).join(' · ') : null;
    }
  }
}

function percent(value: unknown): string {
  return typeof value === 'number' ? `${Math.round(value * 1000) / 10}%` : formatValue(value);
}

/** One-line explanation of why a finding has its outcome. */
export function explainFinding(finding: Pick<Finding, 'outcome' | 'reason' | 'check_name'>): string {
  if (finding.reason) return reasonLabel(finding.reason);
  if (!finding.check_name) return 'Rule has no automatic check — review manually';
  switch (finding.outcome) {
    case 'pass': return 'Meets the rule';
    case 'fail': return 'Does not meet the rule';
    case 'unsure': return 'The tool could not decide — needs a human look';
    default: return 'The tool could not check this rule';
  }
}
