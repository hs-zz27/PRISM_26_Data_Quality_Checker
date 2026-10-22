import { describe, expect, it } from 'vitest';
import { checkLabel, describeRequirement, explainFinding, formatValue, humanizeCode, reasonLabel, reviewStatusLabel, toolStatusLabel } from './labels';

describe('plain-language labels', () => {
  it('maps known codes and falls back to readable text for unknown ones', () => {
    expect(toolStatusLabel('no_issue')).toBe('No issue found');
    expect(reviewStatusLabel('needs_correction')).toBe('Needs correction');
    expect(checkLabel('box_overlaps')).toBe('Box overlap limit');
    expect(reasonLabel('label_not_allowed')).toBe('Label is not in the approved list');
    expect(reasonLabel('brand_new_reason')).toBe('Brand new reason');
    expect(humanizeCode(null)).toBe('—');
  });

  it('describes check requirements with their thresholds', () => {
    expect(describeRequirement('box_overlaps', { max_iou: 0.3 })).toBe('Box overlap (IoU) ≤ 0.300');
    expect(describeRequirement('mask_box_fill', { min_fill_fraction: 0.3, max_fill_fraction: 1 })).toBe('Mask fills 30%–100% of box');
    expect(describeRequirement('allowed_labels', { allowed_labels: ['person', 'bag'] })).toBe('Label must be one of: person, bag');
    expect(describeRequirement('box_validity', {})).toBeNull();
    expect(describeRequirement('unknown_check', { limit: 4 })).toBe('Limit: 4');
  });

  it('explains a finding from its reason, or from its outcome when no reason is given', () => {
    expect(explainFinding({ outcome: 'fail', reason: 'label_not_allowed', check_name: 'allowed_labels' })).toBe('Label is not in the approved list');
    expect(explainFinding({ outcome: 'pass', reason: null, check_name: 'box_validity' })).toBe('Meets the rule');
    expect(explainFinding({ outcome: 'unavailable', reason: null, check_name: null })).toMatch(/review manually/);
  });

  it('formats evidence values compactly', () => {
    expect(formatValue(0.12345)).toBe('0.123');
    expect(formatValue(12.345)).toBe('12.35');
    expect(formatValue(['a', 'b'])).toBe('a, b');
    expect(formatValue(true)).toBe('yes');
    expect(formatValue({ a: 1 })).toBe('{"a":1}');
  });
});
