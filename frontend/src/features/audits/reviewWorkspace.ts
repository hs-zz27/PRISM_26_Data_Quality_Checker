import type { components } from '../../api/generated/openapi';

type Finding = components['schemas']['Finding'];
type Rule = components['schemas']['Rule'];
type SampleAuditSummary = components['schemas']['SampleAuditSummary'];
type SampleReviewDecision = components['schemas']['SampleReviewDecision'];
type FindingResolution = components['schemas']['FindingResolution'];

export type ReviewKeyAction = 'finding-accept' | 'finding-reject' | 'finding-unsure' | 'sample-accept' | 'sample-reject' | 'sample-correction' | 'next' | 'previous' | 'mask' | 'boxes' | 'fit' | 'help' | null;
export function resolveReviewKey(key: string, shiftKey = false): ReviewKeyAction {
  const normalized = key.toLowerCase();
  if (normalized === 'a') return shiftKey ? 'sample-accept' : 'finding-accept';
  if (normalized === 'r') return shiftKey ? 'sample-reject' : 'finding-reject';
  if (normalized === 'u' && !shiftKey) return 'finding-unsure';
  if (normalized === 'n' && shiftKey) return 'sample-correction';
  if (['j', 'arrowright'].includes(normalized)) return 'next';
  if (['k', 'arrowleft'].includes(normalized)) return 'previous';
  if (normalized === 'm') return 'mask';
  if (normalized === 'b') return 'boxes';
  if (normalized === 'f') return 'fit';
  if (key === '?') return 'help';
  return null;
}

export function readPaneWidth(side: 'left' | 'right', fallback: number): number {
  const value = Number(localStorage.getItem(`prism.review.${side}Width`));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}
export function persistPaneWidth(side: 'left' | 'right', width: number): number {
  const bounded = side === 'left' ? Math.min(380, Math.max(200, width)) : Math.min(480, Math.max(280, width));
  localStorage.setItem(`prism.review.${side}Width`, String(bounded));
  return bounded;
}

/**
 * Every override must leave a reason (worklet slide 9): overriding a tool finding,
 * rejecting a sample, or sending it back for correction all require a comment.
 */
export function isReasonRequired(
  decision: { kind: 'finding'; resolution: Exclude<FindingResolution, 'pending'> } | { kind: 'sample'; decision: SampleReviewDecision },
): boolean {
  if (decision.kind === 'finding') return decision.resolution === 'rejected';
  return decision.decision === 'rejected' || decision.decision === 'needs_correction';
}

const ATTENTION_ORDER: Record<string, number> = { fail: 0, unsure: 1, unavailable: 2, pass: 3 };

/** Split findings into the checklist sections: needs attention first, then pre-ticked passes. */
export function groupFindings(findings: Finding[]): { attention: Finding[]; passed: Finding[] } {
  const sorted = [...findings].sort((a, b) =>
    (ATTENTION_ORDER[a.outcome] ?? 9) - (ATTENTION_ORDER[b.outcome] ?? 9)
    || a.rule_id.localeCompare(b.rule_id)
    || (a.object_id ?? 0) - (b.object_id ?? 0));
  return {
    attention: sorted.filter((finding) => finding.outcome !== 'pass'),
    passed: sorted.filter((finding) => finding.outcome === 'pass'),
  };
}

/** Confirmed rules that produced no finding for this sample, so the reviewer can still see them. */
export function rulesWithoutFindings(rules: Rule[] | undefined, findings: Finding[]): Rule[] {
  if (!rules) return [];
  const seen = new Set(findings.map((finding) => finding.rule_id));
  return rules.filter((rule) => rule.confirmation_state === 'confirmed' && !seen.has(rule.rule_id));
}

export interface ReviewProgress { reviewed: number; total: number; pending: number; fraction: number }

export function reviewProgress(items: Pick<SampleAuditSummary, 'review_status'>[]): ReviewProgress {
  const total = items.length;
  const pending = items.filter((item) => item.review_status === 'pending_review').length;
  const reviewed = total - pending;
  return { reviewed, total, pending, fraction: total ? reviewed / total : 0 };
}

/** Index of the next sample still awaiting review after `fromIndex`, wrapping around; -1 if none. */
export function nextUnreviewedIndex(items: Pick<SampleAuditSummary, 'review_status'>[], fromIndex: number): number {
  for (let step = 1; step <= items.length; step += 1) {
    const index = (fromIndex + step + items.length) % items.length;
    if (items[index]?.review_status === 'pending_review') return index;
  }
  return -1;
}
