import { describe, expect, it } from 'vitest';
import { attemptTrail, trailCause, trailFeedback } from './attemptHistory';
import { makeSampleAudit, makeSummary } from '../../test/fixtures';

const attempt = (audit_id: string, created_at: string, sample_revision = 1, reviewer_id: string | null = 'priya') =>
  ({ audit_id, job_id: `job-${audit_id}`, sample_revision, processing_status: 'completed' as const, created_at, reviewer_id });

const revision = (n: number, reviewer_id: string, reason: string) => ({
  dataset_id: 'dataset-1', sample_id: 'sample-1', revision: n, parent_revision: n - 1, objects: [], mask_changed: false,
  reviewer_id, reason, source_audit_id: 'audit-1', created_at: `2026-11-0${n}T09:00:00Z`,
});

describe('attemptTrail', () => {
  // Newest first, as the API lists attempts.
  const attempts = [
    attempt('r02', '2026-11-03T10:00:00Z', 2),
    attempt('r01', '2026-11-02T10:00:00Z', 1),
    attempt('root', '2026-11-01T10:00:00Z', 1, null),
  ];
  const details = {
    root: makeSampleAudit([], {
      audit_id: 'root', summary: makeSummary({ tool_status: 'no_issue', finding_counts: { pass: 3, fail: 0, unsure: 0, unavailable: 0 } }),
      review: { audit_id: 'root', sample_id: 'sample-1', sample_revision: 1, revision: 1, decision: 'needs_correction', reviewer_id: 'sam', comment: 'Feet are cut off', updated_at: '2026-11-01T12:00:00Z' },
    }),
    r01: makeSampleAudit([], {
      audit_id: 'r01',
      measurements: { reviewer_feedback: { reviewer_id: 'priya', source_audit_id: 'root', comment: 'The box misses the feet', propose_correction: false, object_keys: ['1'] } },
    }),
  };

  it('lists attempts oldest first with what led to each one and its verdicts', () => {
    const trail = attemptTrail(attempts, details, [revision(2, 'sam', 'Extended the lower edge')]);
    expect(trail.map((step) => step.auditId)).toEqual(['root', 'r01', 'r02']);

    expect(trail[0]).toMatchObject({ isOriginal: true, isLatest: false, toolStatus: 'no_issue', feedback: null, corrections: [],
      review: { decision: 'needs_correction', by: 'sam', comment: 'Feet are cut off' } });
    expect(trailCause(trail[0]!)).toBe('Original audit');

    expect(trail[1]).toMatchObject({ isOriginal: false, toolStatus: 'flagged', findingCounts: { fail: 1, unsure: 0, unavailable: 0 },
      feedback: { by: 'priya', comment: 'The box misses the feet', suggestCorrection: false, objectKeys: ['1'] },
      review: { decision: 'pending_review', by: null } });
    expect(trailCause(trail[1]!)).toBe('Re-checked with feedback by priya');

    // The correction saved between r01 (revision 1) and r02 (revision 2); r02's detail has not loaded yet.
    expect(trail[2]).toMatchObject({ isLatest: true, revision: 2, toolStatus: null, review: null,
      corrections: [{ revision: 2, by: 'sam', reason: 'Extended the lower edge' }] });
    expect(trailCause(trail[2]!)).toBe('Re-checked after a correction by priya');
  });

  it('names plain rechecks and suggested-correction requests', () => {
    const plain = attemptTrail([attempt('r01', '2026-11-02T10:00:00Z'), attempt('root', '2026-11-01T10:00:00Z', 1, null)], {});
    expect(trailCause(plain[1]!)).toBe('Re-checked by priya');
    const suggest = attemptTrail([attempt('r01', '2026-11-02T10:00:00Z')], {
      r01: makeSampleAudit([], { measurements: { reviewer_feedback: { reviewer_id: 'lee', comment: 'Fix the head', propose_correction: true } } }),
    });
    expect(trailCause(suggest[0]!)).toBe('Re-checked with feedback, asking for a suggested correction by lee');
  });

  it('ignores missing or empty feedback', () => {
    expect(trailFeedback(undefined)).toBeNull();
    expect(trailFeedback({ reviewer_feedback: { comment: '   ' } })).toBeNull();
    expect(trailFeedback({ reviewer_feedback: { comment: 'Check feet' } })).toMatchObject({ by: 'unknown reviewer', objectKeys: [] });
  });
});
