import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { VisualObservations } from './VisualObservations';
import { FeedbackComparison } from './FeedbackComparison';

const comparison = {
  strategy: 'independent-inspection-then-comparison-v1', status: 'completed', model_verified: true,
  failure: null, items: [{ object_key: '/shapes/0', relation: 'supports_concern',
    explanation: 'Advisory interpretation of the recorded text.' }],
};

describe('independent inspection and feedback comparison', () => {
  it('keeps the visual verdict separate even when the advisory comparison disagrees', () => {
    render(<VisualObservations proposal={{ model_verified: true, feedback_comparison: comparison, scope: {
      version: 'target-scope-v1', outcome_source: 'derived_from_target_results',
      results: [{ object_key: '/shapes/0', outcome: 'pass', condition: 'applies',
        reason: 'The box includes the shoes.', flags: [] }], ineligible: [], notes: [], unavailable: null,
    } }} />);
    expect(screen.getByText('/shapes/0 · Pass')).toBeInTheDocument();
    expect(screen.getByText('/shapes/0 · Recorded assessment supports the concern')).toBeInTheDocument();
    expect(screen.getByText(/does not change the visual verdict/)).toBeInTheDocument();
    expect(screen.getByText(/Historical experiment/)).toBeInTheDocument();
  });

  it('reports comparison failure without claiming a new annotation error', () => {
    render(<FeedbackComparison proposal={{ feedback_comparison: {
      ...comparison, status: 'unavailable', model_verified: false, failure: 'inference_unavailable', items: [],
    } }} />);
    expect(screen.getByText(/Feedback comparison unavailable/)).toBeInTheDocument();
  });

  it.each([{}, { feedback_comparison: { ...comparison, model_verified: false } },
    { feedback_comparison: { ...comparison, items: [comparison.items[0], comparison.items[0]] } },
  ])('does not render absent or malformed comparisons', (proposal) => {
    render(<FeedbackComparison proposal={proposal} />);
    expect(screen.queryByRole('region', { name: 'Feedback comparison' })).not.toBeInTheDocument();
  });
});
