import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { VisualObservations } from './VisualObservations';
import { FindingCard } from './FindingCard';
import { makeFinding } from '../../test/fixtures';

const proposal = {
  model_verified: true,
  visual_assessment: { observations: [
    { object_key: '/shapes/0', geometry: ['polygon'], outcome: 'fail', reason: 'The outline misses the visible foot.', region: [20, 70, 30, 80] },
    { object_key: '/shapes/1', geometry: ['polygon'], outcome: 'unsure', reason: 'The hand is obscured.', region: null },
  ] },
};

describe('per-object visual evidence', () => {
  it('shows individual objects, uncertainty and original-coordinate locations', () => {
    render(<VisualObservations proposal={proposal} />);
    expect(screen.getByText('/shapes/0 · Fail')).toBeInTheDocument();
    expect(screen.getByText('/shapes/1 · Unsure')).toBeInTheDocument();
    expect(screen.getByText('The outline misses the visible foot.')).toBeInTheDocument();
    expect(screen.getByText(/Inspect region \[20, 70, 30, 80\]/)).toBeInTheDocument();
    expect(screen.getByText(/do not change the annotation/)).toBeInTheDocument();
  });

  it.each([null, {}, { model_verified: false, visual_assessment: proposal.visual_assessment },
    { model_verified: true, visual_assessment: { observations: [{ outcome: 'pass' }] } },
  ])('leaves older or invalid evidence to the generic fallback', (value) => {
    render(<VisualObservations proposal={value} />);
    expect(screen.queryByRole('region', { name: 'Per-object visual observations' })).not.toBeInTheDocument();
  });

  it('keeps evidence inspectable and reviewer decisions explicit in the audit card', () => {
    const onDecide = vi.fn();
    const finding = makeFinding({ check_name: 'annotation_guideline',
      evidence: {
        image_url: null,
        mask_preview_url: null,
        crop_url: null,
        measurements: { model_proposal: proposal },
      } });
    render(<FindingCard finding={finding} selected={false} onSelect={vi.fn()} onDecide={onDecide}
      historyOpen={false} onToggleHistory={vi.fn()} />);
    expect(screen.getByText('/shapes/0 · Fail')).toBeInTheDocument();
    expect(screen.getByText('Complete recorded evidence')).toBeInTheDocument();
    expect(onDecide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Agree' }));
    expect(onDecide).toHaveBeenCalledWith('accepted');
  });
});
