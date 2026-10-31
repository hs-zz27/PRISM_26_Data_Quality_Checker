import React from 'react';
import { Badge, type BadgeVariant } from '../../components/ui/Badge';
import { formatValue, humanizeCode, outcomeLabel } from '../../lib/labels';

import type { EnclosureEvidenceValue } from './enclosureEvidenceValue';

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
