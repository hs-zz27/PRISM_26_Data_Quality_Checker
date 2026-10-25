/**
 * Which feedback examples can be combined into one candidate policy.
 * ─────────────────────────────────────────────────────────────────────────
 * Mirrors backend validation: every example in a policy shares one dataset and
 * one exact rule pin (rulebook, version, rule and its semantic fingerprint),
 * images are distinct, and a policy holds at most MAX_POLICY_EXAMPLES. The
 * backend still validates on submit; this only explains choices up front.
 * ─────────────────────────────────────────────────────────────────────────
 */

import type { components } from '../../api/generated/openapi';
import { MAX_POLICY_EXAMPLES } from './groundTruth';

export type FeedbackExample = components['schemas']['CaseView'];

/** Examples with equal keys can share a policy. */
export function compatibilityKey(example: FeedbackExample): string {
  const { target } = example;
  return [example.dataset_id, target.rulebook_id, target.rulebook_version, target.rule.rule_id, target.semantic_sha256].join('\u0000');
}

/**
 * Why this example cannot be added to the current selection, or null if it can.
 * `selected` holds the examples already chosen.
 */
export function selectionBlockReason(example: FeedbackExample, selected: FeedbackExample[]): string | null {
  if (selected.some((item) => item.example_id === example.example_id)) return null;
  if (selected.length >= MAX_POLICY_EXAMPLES) return `A policy holds at most ${MAX_POLICY_EXAMPLES} examples. Untick one to choose this.`;
  const first = selected[0];
  if (!first) return null;
  if (example.dataset_id !== first.dataset_id) return 'From a different dataset than the selected examples.';
  if (example.target.rulebook_id !== first.target.rulebook_id) return 'From a different rulebook than the selected examples.';
  if (example.target.rulebook_version !== first.target.rulebook_version) {
    return `From rulebook version ${example.target.rulebook_version}; the selected examples use version ${first.target.rulebook_version}.`;
  }
  if (example.target.rule.rule_id !== first.target.rule.rule_id) {
    return `Judges rule ${example.target.rule.rule_id}; the selected examples judge rule ${first.target.rule.rule_id}.`;
  }
  if (compatibilityKey(example) !== compatibilityKey(first)) return 'The rule wording differs from the selected examples.';
  if (selected.some((item) => item.reference.sample_id === example.reference.sample_id)) {
    return 'Another example from this image is already selected.';
  }
  return null;
}
