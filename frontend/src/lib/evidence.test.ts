import { describe, expect, it } from 'vitest';
import { summarizeEvidence } from './evidence';

// Evidence shapes copied from a real audit of the Penn-Fudan dataset.
const alignmentFail = { detail: null, reason: null, status: 'completed', source_index: 0,
  annotation: { bbox: [163, 141, 291, 427], label: 'PASpersonWalking', object_id: 1 },
  result: { reason: null, mask_bbox: [188, 141, 291, 427], object_id: 1, annotation_bbox: [163, 141, 291, 427],
    edge_differences: { top: 0, left: -25, right: 0, bottom: 0 }, max_edge_difference: 25, measurement_available: true } };
const maskIdsFail = { detail: null, reason: null, status: 'completed',
  result: { matches: false, mask_ids: [1, 2], missing_masks: [], annotation_ids: [1], missing_annotations: [2] } };
const outsideFail = { detail: null, reason: null, status: 'completed', source_index: 0,
  result: { reason: null, object_id: 1, outside_pixels: 2434, annotation_bbox: [266, 69, 425, 354],
    outside_fraction: 0.14195730782689842, total_mask_pixels: 17146, measurement_available: true } };
const validityFail = { detail: null, reason: null, status: 'completed',
  result: { bbox: [43, 23, 520, 326], valid: false, detail: 'Box coordinates must lie within the image bounds.', reason: 'box_outside_image' } };
const overlapSkipped = { detail: null, reason: null, status: 'completed', pair_index: 2,
  result: { iou: null, box1: [43, 23, 520, 326], box2: [273, 81, 389, 327], reason: 'invalid_box', object_ids: [1, 4],
    union_area: null, invalid_boxes: { box1: 'box_outside_image' }, intersection_area: null, measurement_available: false } };
const labelFail = { annotation: { bbox: [142, 121, 264, 379], label: 'car', object_id: 1 }, actual_label: 'car',
  allowed_labels: ['PASpersonWalking', 'PASpersonStanding'] };
const unavailable = { detail: null, reason: 'box_outside_image', status: 'unavailable',
  annotation: { bbox: [33, 203, 595, 484], label: 'PASpersonWalking', object_id: 1 }, source_index: 0 };

const asMap = (rows: { label: string; value: string }[]) => Object.fromEntries(rows.map((row) => [row.label, row.value]));

describe('readable evidence', () => {
  it('shows box fit as edge gaps in pixels', () => {
    expect(asMap(summarizeEvidence('box_mask_alignment', alignmentFail))).toEqual({
      'Largest edge gap': '25 px',
      'Edge gaps': 'left 25 px, right 0 px, top 0 px, bottom 0 px',
      'Annotated box': '[163, 141, 291, 427]',
      'Person outline (mask)': '[188, 141, 291, 427]',
    });
  });

  it('names mask regions that have no box', () => {
    expect(asMap(summarizeEvidence('mask_ids', maskIdsFail))).toEqual({
      'Mask regions': '#1, #2', 'Annotated objects': '#1', 'Mask regions with no box': '#2',
    });
  });

  it('reports mask spill as a percentage of pixels', () => {
    expect(asMap(summarizeEvidence('mask_outside_box', outsideFail))['Mask outside box']).toBe('14.2% (2434 of 17146 pixels)');
  });

  it('explains invalid boxes and skipped overlap measurements', () => {
    expect(asMap(summarizeEvidence('box_validity', validityFail)).Problem).toBe('Box lies partly outside the image');
    expect(asMap(summarizeEvidence('box_overlaps', overlapSkipped))).toEqual({
      'Objects compared': '#1, #4', 'Overlap (IoU)': 'not measured', 'Why not measured': 'Box coordinates are invalid or outside the image',
    });
  });

  it('shows the label against the allowed list', () => {
    expect(asMap(summarizeEvidence('allowed_labels', labelFail))).toEqual({
      Label: 'car', 'Allowed labels': 'PASpersonWalking, PASpersonStanding',
    });
  });

  it('adds nothing when a check could not run, and flattens unknown checks', () => {
    expect(summarizeEvidence('mask_box_fill', unavailable)).toEqual([]);
    const rows = summarizeEvidence('some_future_check', { status: 'completed', result: { score: 0.5, note: 'ok' } });
    expect(asMap(rows)).toEqual({ 'Result · score': '0.500', 'Result · note': 'ok' });
  });
});
