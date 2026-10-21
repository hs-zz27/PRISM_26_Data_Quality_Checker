import { beforeEach, describe, expect, it } from 'vitest';
import { persistPaneWidth, readPaneWidth, resolveReviewKey } from './reviewWorkspace';

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
