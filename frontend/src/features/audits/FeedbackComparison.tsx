import type { components } from '../../api/generated/openapi';

type Comparison = components['schemas']['ReviewerFeedbackComparison'] & {
  items: NonNullable<components['schemas']['ReviewerFeedbackComparison']['items']>;
};

const LABELS: Record<components['schemas']['FeedbackComparisonItem']['relation'], string> = {
  supports_concern: 'Recorded assessment supports the concern',
  does_not_support_concern: 'Recorded assessment does not support the concern',
  unclear: 'Comparison unresolved',
  not_applicable: 'Guideline condition not applicable',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readComparison(proposal: unknown): Comparison | null {
  if (!isRecord(proposal) || !isRecord(proposal.feedback_comparison)) return null;
  const value = proposal.feedback_comparison;
  if (value.strategy !== 'independent-inspection-then-comparison-v1'
    || !['completed', 'unavailable'].includes(String(value.status))
    || typeof value.model_verified !== 'boolean'
    || (value.failure != null && typeof value.failure !== 'string')
    || !Array.isArray(value.items) || value.items.length > 8) return null;
  if ((value.status === 'unavailable') !== (typeof value.failure === 'string')) return null;
  const keys = new Set<string>();
  for (const item of value.items) {
    if (!isRecord(item) || typeof item.object_key !== 'string' || keys.has(item.object_key)
      || typeof item.relation !== 'string' || !Object.hasOwn(LABELS, item.relation)
      || typeof item.explanation !== 'string') return null;
    if (['supports_concern', 'does_not_support_concern'].includes(item.relation)
      && (value.model_verified !== true || value.status !== 'completed')) return null;
    keys.add(item.object_key);
  }
  return value as unknown as Comparison;
}

/** Feedback interpretation is separate from the image-based verdict and reviewer decision. */
export function FeedbackComparison({ proposal }: { proposal: unknown }) {
  const comparison = readComparison(proposal);
  if (!comparison) return null;
  return (
    <section className="finding-evidence" aria-label="Feedback comparison">
      <span className="finding-section-label">Feedback comparison</span>
      <p className="text-xs">Historical experiment: this text interpretation did not visually verify the reviewer’s concern.</p>
      <p className="text-xs">The visual inspection did not see the reviewer comment. This later text comparison is advisory and does not change the visual verdict.</p>
      {comparison.status === 'unavailable' && (
        <p className="text-xs">Feedback comparison unavailable ({comparison.failure}). The independent visual results remain recorded above.</p>
      )}
      <ul className="space-y-2 text-xs">
        {comparison.items.map((item) => (
          <li key={item.object_key}>
            <b>{item.object_key} · {LABELS[item.relation]}</b>
            <p>{item.explanation}</p>
          </li>
        ))}
      </ul>
      <p className="text-2xs text-text-muted">The independent assessment can still be wrong. Review the image before deciding.</p>
    </section>
  );
}
