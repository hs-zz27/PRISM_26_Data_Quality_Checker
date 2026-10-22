import type { components } from '../../api/generated/openapi';

type SampleAudit = components['schemas']['SampleAudit'];
type Finding = components['schemas']['Finding'];

export interface CandidateCase {
  key: string;
  audit_id: string;
  sample_id: string;
  sample_revision: number;
  rule_id: string;
  description: string;
  /** Expected outcome implied by the reviewer's decisions; null when the reviewer has not settled it. */
  expected: 'pass' | 'fail' | null;
  reason: string;
  findingCount: number;
}

export const MAX_GROUND_TRUTH_CASES = 100;
export const MAX_POLICY_EXAMPLES = 4;

/** What the reviewer says the right answer is for one finding, or null if they did not settle it. */
export function reviewerExpectation(finding: Pick<Finding, 'outcome' | 'review'>): 'pass' | 'fail' | null {
  const resolution = finding.review?.resolution;
  if (finding.outcome !== 'pass' && finding.outcome !== 'fail') return null;
  if (resolution === 'accepted') return finding.outcome;
  if (resolution === 'rejected') return finding.outcome === 'pass' ? 'fail' : 'pass';
  return null;
}

/**
 * Turn reviewed findings into rule-level adjudication candidates (one per sample × rule).
 * A rule fails for the sample if the reviewer says any of its object findings should fail.
 */
export function deriveCandidateCases(samples: SampleAudit[]): CandidateCase[] {
  const cases = new Map<string, CandidateCase>();
  for (const sample of samples) {
    const revision = sample.summary.sample_revision;
    for (const finding of sample.findings) {
      const key = `${sample.audit_id}|${sample.summary.sample_id}|${revision}|${finding.rule_id}`;
      const expectation = reviewerExpectation(finding);
      const existing = cases.get(key);
      const comment = finding.review?.comment?.trim();
      if (!existing) {
        cases.set(key, {
          key,
          audit_id: sample.audit_id,
          sample_id: sample.summary.sample_id,
          sample_revision: revision,
          rule_id: finding.rule_id,
          description: finding.description,
          expected: expectation,
          reason: comment || defaultReason(expectation, finding.review?.resolution),
          findingCount: 1,
        });
        continue;
      }
      existing.findingCount += 1;
      if (expectation === 'fail' || (expectation === 'pass' && existing.expected === null)) existing.expected = expectation;
      if (comment && !existing.reason.includes(comment)) existing.reason = existing.reason ? `${existing.reason}; ${comment}` : comment;
    }
  }
  return [...cases.values()].sort((a, b) => a.sample_id.localeCompare(b.sample_id) || a.rule_id.localeCompare(b.rule_id));
}

function defaultReason(expected: 'pass' | 'fail' | null, resolution: string | undefined): string {
  if (expected === null) return '';
  return resolution === 'rejected' ? `Reviewer overrode the tool: should ${expected}` : `Reviewer confirmed the tool: ${expected}`;
}
