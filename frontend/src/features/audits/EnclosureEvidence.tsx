import React from 'react';
import { Badge, type BadgeVariant } from '../../components/ui/Badge';
import { formatValue, humanizeCode, outcomeLabel } from '../../lib/labels';

interface BoxEvidence {
  annotation_index: number;
  box_number: number;
  object_id: number;
  outcome: string;
  model_verified: boolean;
  reason: string | null;
  bbox?: unknown;
}

interface EnclosureEvidenceValue {
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

const variants: Record<string, BadgeVariant> = {
  pass: 'pass', fail: 'fail', unsure: 'unsure', unavailable: 'unavailable',
};

export const EnclosureEvidence: React.FC<{ evidence: EnclosureEvidenceValue }> = ({ evidence }) => (
  <section className="enclosure-evidence" aria-label="Pedestrian enclosure evidence">
    <p className="finding-section-label">Per-box model proposals</p>
    <p>These results await human review. A partly visible pedestrian inside the box counts.</p>
    <p>{evidence.boxes.length} recorded / {evidence.boxes_intended ?? 'unknown'} intended boxes</p>
    {evidence.failure && <p role="status">{humanizeCode(evidence.failure)}</p>}
    <ol className="space-y-2 mt-2">
      {evidence.boxes.map((box, index) => (
        <li key={`${box.annotation_index}-${index}`} className="rounded border border-border p-2 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <b>Box {box.box_number} · Object #{box.object_id}</b>
            <Badge variant={variants[box.model_verified ? box.outcome : 'unavailable'] ?? 'unavailable'} size="sm">
              {outcomeLabel(box.model_verified ? box.outcome : 'unavailable')}
            </Badge>
          </div>
          <p>{box.reason ? (/^[a-z]+(?:_[a-z]+)+$/.test(box.reason) ? humanizeCode(box.reason) : box.reason) : 'No explanation recorded'}</p>
          <p className="text-xs text-text-muted">Coordinates: {formatValue(box.bbox)} · {box.model_verified ? 'Model identity verified' : 'No verified model verdict'}</p>
        </li>
      ))}
    </ol>
  </section>
);
