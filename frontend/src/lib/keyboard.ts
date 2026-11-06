/**
 * Keyboard shortcut registry.
 * ─────────────────────────────────────────────────────────────────────────
 * Shortcuts are registered here for discoverability (shown in ? help modal).
 * Components import and register handlers via useEffect / addEventListener.
 *
 * Rule: shortcuts are DISABLED when focus is inside a text field.
 * The `isTypingTarget` helper is used to enforce this.
 * ─────────────────────────────────────────────────────────────────────────
 */

export type ShortcutCategory = 'review' | 'navigation' | 'canvas' | 'global';

export interface Shortcut {
  key: string;
  display: string;         // Human-readable key label
  description: string;
  category: ShortcutCategory;
}

/** All registered shortcuts for the Help modal. */
export const SHORTCUTS: Shortcut[] = [
  // Review
  { key: 'a', display: 'A', description: 'Agree with the selected finding', category: 'review' },
  { key: 'r', display: 'R', description: 'Override the selected finding (reason required)', category: 'review' },
  { key: 'u', display: 'U', description: 'Mark selected finding unsure', category: 'review' },
  { key: 'Shift+A', display: 'Shift+A', description: 'Accept sample', category: 'review' },
  { key: 'Shift+R', display: 'Shift+R', description: 'Reject sample (reason required)', category: 'review' },
  { key: 'Shift+N', display: 'Shift+N', description: 'Send sample for correction (reason required)', category: 'review' },
  // Navigation
  { key: 'ArrowLeft',  display: '←',   description: 'Previous sample', category: 'navigation' },
  { key: 'ArrowRight', display: '→',   description: 'Next sample',     category: 'navigation' },
  { key: 'j',          display: 'J',   description: 'Next sample',     category: 'navigation' },
  { key: 'k',          display: 'K',   description: 'Previous sample', category: 'navigation' },
  // Canvas
  { key: 'm', display: 'M', description: 'Toggle mask overlay',   category: 'canvas' },
  { key: 'b', display: 'B', description: 'Toggle box overlays',   category: 'canvas' },
  { key: 'f', display: 'F', description: 'Fit image to view',     category: 'canvas' },
  { key: 'Space', display: 'Space + drag', description: 'Pan image', category: 'canvas' },
  { key: '+', display: '+', description: 'Zoom in',               category: 'canvas' },
  { key: '-', display: '-', description: 'Zoom out',              category: 'canvas' },
  // Global
  { key: '?', display: '?', description: 'Show keyboard shortcuts', category: 'global' },
];

/**
 * Returns true if the current focus target is a text input, textarea,
 * or contenteditable element — in which case review shortcuts are suppressed.
 */
export function isTypingTarget(event: KeyboardEvent): boolean {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;

  const tag = target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea') return true;
  if (target.isContentEditable) return true;

  return false;
}

