import type { components } from '../../api/generated/openapi';

type SampleAttempt = components['schemas']['SampleAttempt'];
type SampleAudit = components['schemas']['SampleAudit'];
type AnnotationRevision = components['schemas']['AnnotationRevision'];

export interface TrailFeedback {
  by: string;
  comment: string;
  suggestCorrection: boolean;
  objectKeys: string[];
}

export interface TrailCorrection {
  revision: number;
  by: string;
  reason: string;
  at: string;
}

export interface TrailStep {
  auditId: string;
  createdAt: string;
  revision: number;
  processingStatus: SampleAttempt['processing_status'];
  isLatest: boolean;
  isOriginal: boolean;
  /** Who started this attempt; null for the original audit. */
  requestedBy: string | null;
  /** Feedback this attempt was asked to verify, with who gave it. */
  feedback: TrailFeedback | null;
  /** Annotation corrections saved since the previous attempt, oldest first. */
  corrections: TrailCorrection[];
  /** Null until this attempt's detail has loaded. */
  toolStatus: string | null;
  findingCounts: { fail: number; unsure: number; unavailable: number } | null;
  review: { decision: string; by: string | null; comment: string | null; at: string | null } | null;
}

export function trailFeedback(measurements: unknown): TrailFeedback | null {
  if (!measurements || typeof measurements !== 'object') return null;
  const value = (measurements as Record<string, unknown>).reviewer_feedback;
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (typeof row.comment !== 'string' || !row.comment.trim()) return null;
  return {
    by: typeof row.reviewer_id === 'string' && row.reviewer_id ? row.reviewer_id : 'unknown reviewer',
    comment: row.comment,
    suggestCorrection: row.propose_correction === true,
    objectKeys: Array.isArray(row.object_keys) ? row.object_keys.filter((key): key is string => typeof key === 'string') : [],
  };
}

/**
 * One image's attempts, oldest first, with what led to each one and its verdicts.
 * `details` maps an attempt's audit ID to that attempt's own sample result (read without rechecks).
 */
export function attemptTrail(
  attempts: readonly SampleAttempt[],
  details: Readonly<Record<string, SampleAudit | undefined>>,
  revisions: readonly AnnotationRevision[] = [],
): TrailStep[] {
  const ordered = [...attempts].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const latestId = ordered.at(-1)?.audit_id;
  return ordered.map((attempt, index) => {
    const detail = details[attempt.audit_id];
    const previousRevision = index > 0 ? ordered[index - 1]!.sample_revision : null;
    const corrections = previousRevision === null ? [] : revisions
      .filter((rev) => rev.revision > previousRevision && rev.revision <= attempt.sample_revision)
      .sort((a, b) => a.revision - b.revision)
      .map((rev) => ({ revision: rev.revision, by: rev.reviewer_id ?? 'unknown reviewer', reason: rev.reason ?? '', at: rev.created_at }));
    const review = detail?.review;
    const decided = review && review.decision !== 'pending_review';
    return {
      auditId: attempt.audit_id,
      createdAt: attempt.created_at,
      revision: attempt.sample_revision,
      processingStatus: attempt.processing_status,
      isLatest: attempt.audit_id === latestId,
      isOriginal: index === 0 && !attempt.reviewer_id,
      requestedBy: attempt.reviewer_id ?? null,
      feedback: trailFeedback(detail?.measurements),
      corrections,
      toolStatus: detail ? detail.summary.tool_status : null,
      findingCounts: detail ? {
        fail: detail.summary.finding_counts.fail,
        unsure: detail.summary.finding_counts.unsure,
        unavailable: detail.summary.finding_counts.unavailable,
      } : null,
      review: detail ? (decided
        ? { decision: review.decision, by: review.reviewer_id ?? null, comment: review.comment ?? null, at: review.updated_at ?? null }
        : { decision: 'pending_review', by: null, comment: null, at: null }) : null,
    };
  });
}

/** Plain-language cause of an attempt, e.g. "Re-checked with feedback from priya". */
export function trailCause(step: TrailStep): string {
  if (step.isOriginal) return 'Original audit';
  const who = step.feedback?.by ?? step.requestedBy;
  const suffix = who ? ` by ${who}` : '';
  if (step.feedback) return step.feedback.suggestCorrection
    ? `Re-checked with feedback, asking for a suggested correction${suffix}`
    : `Re-checked with feedback${suffix}`;
  if (step.corrections.length) return `Re-checked after a correction${suffix}`;
  return `Re-checked${suffix}`;
}
