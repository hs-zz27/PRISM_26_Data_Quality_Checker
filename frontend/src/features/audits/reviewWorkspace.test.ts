import { beforeEach, describe, expect, it } from 'vitest';
import {
  groupFindings,
  isReasonRequired,
  nextUnreviewedIndex,
  persistPaneWidth,
  readPaneWidth,
  resolveReviewKey,
  reviewProgress,
  rulesWithoutFindings,
} from './reviewWorkspace';
import { makeFinding, makeRule, makeSummary } from '../../test/fixtures';

describe('review workstation controls', () => {
  beforeEach(() => localStorage.clear());
  it('persists and bounds both resizable pane widths', () => {
    expect(persistPaneWidth('left', 500)).toBe(380);
    expect(readPaneWidth('left', 248)).toBe(380);
    expect(persistPaneWidth('right', 100)).toBe(280);
    expect(readPaneWidth('right', 344)).toBe(280);
  });
  it('maps finding and sample shortcuts to separate actions', () => {
    expect(resolveReviewKey('a')).toBe('finding-accept');
    expect(resolveReviewKey('r')).toBe('finding-reject');
    expect(resolveReviewKey('u')).toBe('finding-unsure');
    expect(resolveReviewKey('A', true)).toBe('sample-accept');
    expect(resolveReviewKey('R', true)).toBe('sample-reject');
    expect(resolveReviewKey('N', true)).toBe('sample-correction');
    expect(resolveReviewKey('f')).toBe('fit');
    expect(resolveReviewKey('?')).toBe('help');
  });
});

describe('reason requirement', () => {
  it('requires a reason for every override, rejection and correction request', () => {
    expect(isReasonRequired({ kind: 'finding', resolution: 'rejected' })).toBe(true);
    expect(isReasonRequired({ kind: 'finding', resolution: 'accepted' })).toBe(false);
    expect(isReasonRequired({ kind: 'finding', resolution: 'unsure' })).toBe(false);
    expect(isReasonRequired({ kind: 'sample', decision: 'rejected' })).toBe(true);
    expect(isReasonRequired({ kind: 'sample', decision: 'needs_correction' })).toBe(true);
    expect(isReasonRequired({ kind: 'sample', decision: 'accepted' })).toBe(false);
  });
});

describe('finding checklist', () => {
  it('puts failures first, then unsure and unavailable, and keeps passes separate', () => {
    const findings = [
      makeFinding({ finding_id: 'p', outcome: 'pass', rule_id: 'A' }),
      makeFinding({ finding_id: 'u', outcome: 'unsure', rule_id: 'A' }),
      makeFinding({ finding_id: 'f2', outcome: 'fail', rule_id: 'B' }),
      makeFinding({ finding_id: 'na', outcome: 'unavailable', rule_id: 'A' }),
      makeFinding({ finding_id: 'f1', outcome: 'fail', rule_id: 'A' }),
    ];
    const { attention, passed } = groupFindings(findings);
    expect(attention.map((f) => f.finding_id)).toEqual(['f1', 'f2', 'u', 'na']);
    expect(passed.map((f) => f.finding_id)).toEqual(['p']);
  });

  it('lists confirmed rules that produced no finding, ignoring rejected ones', () => {
    const rules = [makeRule({ rule_id: 'A' }), makeRule({ rule_id: 'B' }), makeRule({ rule_id: 'C', confirmation_state: 'rejected' })];
    expect(rulesWithoutFindings(rules, [makeFinding({ rule_id: 'A' })]).map((r) => r.rule_id)).toEqual(['B']);
    expect(rulesWithoutFindings(undefined, [])).toEqual([]);
  });
});

describe('queue progress', () => {
  const queue = [
    makeSummary({ sample_id: 'a', review_status: 'accepted' }),
    makeSummary({ sample_id: 'b', review_status: 'pending_review' }),
    makeSummary({ sample_id: 'c', review_status: 'rejected' }),
    makeSummary({ sample_id: 'd', review_status: 'pending_review' }),
  ];

  it('counts reviewed samples', () => {
    expect(reviewProgress(queue)).toEqual({ reviewed: 2, total: 4, pending: 2, fraction: 0.5 });
    expect(reviewProgress([]).fraction).toBe(0);
  });

  it('finds the next sample awaiting review, wrapping around', () => {
    expect(nextUnreviewedIndex(queue, 0)).toBe(1);
    expect(nextUnreviewedIndex(queue, 1)).toBe(3);
    expect(nextUnreviewedIndex(queue, 3)).toBe(1);
    expect(nextUnreviewedIndex([makeSummary({ review_status: 'accepted' })], 0)).toBe(-1);
  });
});
