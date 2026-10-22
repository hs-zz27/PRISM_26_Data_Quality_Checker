import { describe, expect, it } from 'vitest';
import { deriveCandidateCases, reviewerExpectation } from './groundTruth';
import { makeFinding, makeSampleAudit } from '../../test/fixtures';

const review = (resolution: 'accepted' | 'rejected' | 'unsure' | 'pending', comment = '') => ({ revision: 1, resolution, reviewer_id: 'ana', comment, updated_at: null });

describe('ground truth from reviewer decisions', () => {
  it('reads the expected result from agree / override decisions', () => {
    expect(reviewerExpectation({ outcome: 'fail', review: review('accepted') })).toBe('fail');
    expect(reviewerExpectation({ outcome: 'fail', review: review('rejected') })).toBe('pass');
    expect(reviewerExpectation({ outcome: 'pass', review: review('rejected') })).toBe('fail');
    expect(reviewerExpectation({ outcome: 'pass', review: review('unsure') })).toBeNull();
    expect(reviewerExpectation({ outcome: 'unsure', review: review('accepted') })).toBeNull();
  });

  it('creates one case per sample and rule; any failing object fails the rule', () => {
    const sample = makeSampleAudit([
      makeFinding({ finding_id: 'a', object_id: 1, rule_id: 'BOX', outcome: 'pass', review: review('accepted') }),
      makeFinding({ finding_id: 'b', object_id: 2, rule_id: 'BOX', outcome: 'pass', review: review('rejected', 'Box 2 misses the arm') }),
      makeFinding({ finding_id: 'c', object_id: 1, rule_id: 'LBL', outcome: 'unsure', review: review('unsure') }),
    ]);
    const cases = deriveCandidateCases([sample]);
    expect(cases).toHaveLength(2);
    const box = cases.find((c) => c.rule_id === 'BOX');
    expect(box).toMatchObject({ expected: 'fail', findingCount: 2, sample_revision: 1, audit_id: 'audit-1' });
    expect(box?.reason).toContain('Box 2 misses the arm');
    expect(cases.find((c) => c.rule_id === 'LBL')?.expected).toBeNull();
  });
});
