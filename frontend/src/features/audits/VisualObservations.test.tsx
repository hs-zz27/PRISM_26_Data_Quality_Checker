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
  it('shows conflicting applicability as unsure rather than hiding it as not applicable', () => {
    render(<VisualObservations proposal={{ model_verified: true, scope: {
      version: 'target-scope-v1', outcome_source: 'derived_from_target_results',
      results: [{ object_key: '/shapes/0', geometry: 'polygon', outcome: 'unsure',
        condition: 'not_present', model_compliance: 'no',
        reason: 'The polygon is displaced into background.', flags: ['condition_compliance_conflict'] }],
      ineligible: [], notes: [], unavailable: null,
    } }} />);
    expect(screen.getByText('/shapes/0 · Unsure')).toBeInTheDocument();
    expect(screen.queryByText('/shapes/0 · Not applicable')).not.toBeInTheDocument();
    expect(screen.getByText(/conflicting applicability and compliance/)).toBeInTheDocument();
    expect(screen.getByText('The polygon is displaced into background.')).toBeInTheDocument();
  });

  it('keeps legitimate exclusions visible alongside the overall review requirement', () => {
    const note = 'Other targets passed, but a model-only applicability exclusion needs reviewer confirmation.';
    render(<VisualObservations proposal={{ model_verified: true, scope: {
      version: 'target-scope-v1', outcome_source: 'derived_from_target_results',
      results: [
        { object_key: '/shapes/0', outcome: 'pass', condition: 'applies', flags: [] },
        { object_key: '/shapes/1', outcome: 'unavailable', condition: 'not_present',
          reason: 'This is a skateboard.', flags: ['condition_not_present'] },
      ], ineligible: [], notes: [note], unavailable: null,
    } }} />);
    expect(screen.getByText('/shapes/1 · Not applicable')).toBeInTheDocument();
    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it('labels absent conditions as not applicable and keeps contradictory prose in raw evidence', () => {
    const rawReason = 'The skateboard box excludes the shoes and violates the guideline.';
    render(<VisualObservations proposal={{ model_verified: true, scope: {
      version: 'target-scope-v1', outcome_source: 'derived_from_target_results',
      results: [
        { object_key: '/annotations/0', geometry: 'box', outcome: 'fail', condition: 'applies',
          reason: 'The person box excludes the shoes.', flags: [] },
        { object_key: '/annotations/5', geometry: 'box', outcome: 'unavailable', condition: 'not_present',
          reason: rawReason, flags: ['condition_not_present'] },
        { object_key: '/annotations/6', outcome: 'unavailable', reason: null, flags: ['call_failed'] },
      ], ineligible: [], notes: [], unavailable: null,
    } }} />);
    expect(screen.getByText('/annotations/0 · Fail')).toBeInTheDocument();
    expect(screen.getByText('/annotations/5 · Not applicable')).toBeInTheDocument();
    expect(screen.queryByText('/annotations/5 · Unavailable')).not.toBeInTheDocument();
    expect(screen.getByText('/annotations/6 · Unavailable')).toBeInTheDocument();
    const details = screen.getByText('Raw model explanation').closest('details');
    expect(details).not.toHaveAttribute('open');
    expect(details).toContainElement(screen.getByText(rawReason));
    expect(screen.getByText(/No compliance verdict was counted/)).toBeInTheDocument();
  });

  it('shows scoped results and separates context annotations from judged objects', () => {
    render(<VisualObservations proposal={{ model_verified: true, scope: {
      version: 'target-scope-v1', outcome_source: 'derived_from_target_results',
      results: [{ object_key: '/shapes/0', geometry: 'polygon', outcome: 'unsure',
        reason: 'The visible boundary is unclear.', condition: 'unclear', flags: ['condition_unclear'] }],
      ineligible: [{ object_key: '/shapes/1', detail: 'This object has no supplied polygon.' }],
      notes: ['The overall result follows the per-object results.'], unavailable: null,
    } }} />);
    expect(screen.getByText('/shapes/0 · Unsure')).toBeInTheDocument();
    expect(screen.getByText('/shapes/1 · Not assessed for this guideline')).toBeInTheDocument();
    expect(screen.getByText('The visible boundary is unclear.')).toBeInTheDocument();
    expect(screen.getByText(/condition_unclear/)).toBeInTheDocument();
    expect(screen.queryByText(/Inspect region/)).not.toBeInTheDocument();
  });

  it('explains an ineligible guideline even when no model call was made', () => {
    render(<VisualObservations proposal={{ model_verified: false, scope: {
      version: 'target-scope-v1', outcome_source: 'no_model_call', results: [], notes: [],
      unavailable: { code: 'supplied_box_not_available', detail: 'No supplied bounding box exists.' },
      ineligible: [{ object_key: '/shapes/0', detail: 'Its box was derived from a polygon.' }],
    } }} />);
    expect(screen.getByText('No supplied bounding box exists.')).toBeInTheDocument();
    expect(screen.getByText('/shapes/0 · Not assessed for this guideline')).toBeInTheDocument();
  });

  it('does not present unverified scoped model judgments as observations', () => {
    render(<VisualObservations proposal={{ model_verified: false, scope: {
      version: 'target-scope-v1', outcome_source: 'derived_from_target_results',
      results: [{ object_key: '/shapes/0', outcome: 'pass', flags: [] }], ineligible: [], notes: [],
    } }} />);
    expect(screen.queryByRole('region', { name: 'Per-object visual observations' })).not.toBeInTheDocument();
  });

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
