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
