interface BoxEvidence {
  annotation_index: number;
  box_number: number;
  object_id: number;
  outcome: string;
  model_verified: boolean;
  reason: string | null;
  bbox?: unknown;
}

export interface EnclosureEvidenceValue {
  boxes: BoxEvidence[];
  boxes_intended: number | null;
  failure?: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Unknown evidence versions retain the generic evidence display instead of losing rows. */
export function readEnclosureEvidence(value: unknown): EnclosureEvidenceValue | null {
  if (!isRecord(value) || !Array.isArray(value.boxes)) return null;
  if (value.boxes_intended !== null && !Number.isInteger(value.boxes_intended)) return null;
  const boxes: BoxEvidence[] = [];
  for (const box of value.boxes) {
    if (!isRecord(box) || !Number.isInteger(box.annotation_index) || !Number.isInteger(box.box_number)
      || !Number.isInteger(box.object_id) || typeof box.outcome !== 'string'
      || !['pass', 'fail', 'unsure', 'unavailable'].includes(box.outcome)
      || typeof box.model_verified !== 'boolean'
      || (box.reason !== null && typeof box.reason !== 'string')) return null;
    boxes.push({ annotation_index: box.annotation_index as number, box_number: box.box_number as number,
      object_id: box.object_id as number, outcome: box.outcome, model_verified: box.model_verified,
      reason: box.reason, bbox: box.bbox });
  }
  return { boxes, boxes_intended: value.boxes_intended as number | null,
    failure: typeof value.failure === 'string' ? value.failure : null };
}

