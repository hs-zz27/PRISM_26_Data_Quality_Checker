import type { components } from '../../api/generated/openapi';
import { parseJsonGeometry } from '../corrections/jsonGeometry';

type Objects = components['schemas']['CorrectionRequest']['objects'];
export function reviewFeedbackComment(value: unknown): string | null {
  return value && typeof value === 'object' && 'comment' in value && typeof value.comment === 'string' ? value.comment : null;
}
export interface CorrectionPreview {
  dataset_id: string; sample_id: string; sample_revision: number;
  status: string; reason: string; feedback: { comment: string }; objects: Objects | null;
}
export function correctionPreview(value: unknown): CorrectionPreview | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (row.protocol !== 'review-correction-v1' || typeof row.dataset_id !== 'string'
    || typeof row.sample_id !== 'string' || typeof row.sample_revision !== 'number'
    || typeof row.status !== 'string' || typeof row.reason !== 'string'
    || !row.feedback || typeof row.feedback !== 'object' || !('comment' in row.feedback)
    || typeof row.feedback.comment !== 'string') return null;
  let objects: Objects | null = null;
  if (row.status === 'proposed') {
    if (!Array.isArray(row.objects)) return null;
    try {
      if (row.annotation_format === 'coco' || row.annotation_format === 'labelme') {
        objects = parseJsonGeometry(JSON.stringify(row.objects));
      } else if (row.annotation_format === 'penn_fudan') {
        objects = row.objects.map((obj: unknown) => {
          const item = obj as Record<string, unknown>;
          if (!item || !Number.isInteger(item.object_id) || typeof item.label !== 'string'
            || !Array.isArray(item.bbox) || item.bbox.length !== 4 || !item.bbox.every(Number.isInteger)) {
            throw new Error('Invalid proposal geometry');
          }
          return { object_id: item.object_id as number, label: item.label,
            bbox: item.bbox as [number, number, number, number] };
        });
      } else return null;
    } catch { return null; }
  }
  return { dataset_id: row.dataset_id, sample_id: row.sample_id, sample_revision: row.sample_revision,
    status: row.status, reason: row.reason, feedback: { comment: row.feedback.comment }, objects };
}
