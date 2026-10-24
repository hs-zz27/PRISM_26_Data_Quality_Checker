import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EnclosureEvidence, readEnclosureEvidence } from './EnclosureEvidence';

const box = { annotation_index: 0, box_number: 1, object_id: 4,
  outcome: 'fail', model_verified: true, reason: 'Background only', bbox: [1, 1, 80, 55] };

describe('enclosure evidence', () => {
  it('shows every box including duplicate object IDs and unsent proposals', () => {
    const evidence = readEnclosureEvidence({ boxes_intended: 2, boxes: [box,
      { ...box, annotation_index: 1, box_number: 2, outcome: 'unavailable',
        model_verified: false, reason: 'sample_budget_exhausted' }] });
    expect(evidence).not.toBeNull();
    render(<EnclosureEvidence evidence={evidence!} />);
    expect(screen.getByText('Box 1 · Object #4')).toBeInTheDocument();
    expect(screen.getByText('Box 2 · Object #4')).toBeInTheDocument();
    expect(screen.getByText('Sample budget exhausted')).toBeInTheDocument();
    expect(screen.getByText(/await human review/)).toBeInTheDocument();
  });

  it('keeps empty evidence failures visible', () => {
    render(<EnclosureEvidence evidence={{ boxes: [], boxes_intended: 0, failure: 'no_annotation_boxes' }} />);
    expect(screen.getByText('No annotation boxes')).toBeInTheDocument();
  });

  it('does not display an unverified proposal as a pass', () => {
    render(<EnclosureEvidence evidence={{ boxes: [{ ...box, outcome: 'pass', model_verified: false }], boxes_intended: 1 }} />);
    expect(screen.getByText('Could not check')).toBeInTheDocument();
    expect(screen.queryByText('Pass')).not.toBeInTheDocument();
  });

  it('falls back to raw evidence if any box has an unknown shape', () => {
    expect(readEnclosureEvidence({ boxes_intended: 2, boxes: [box, {}] })).toBeNull();
    expect(readEnclosureEvidence(null)).toBeNull();
  });
});
