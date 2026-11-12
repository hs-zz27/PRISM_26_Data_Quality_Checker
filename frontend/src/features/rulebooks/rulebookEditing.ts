import type { components } from '../../api/generated/openapi';

type Rule = components['schemas']['Rule'];

export interface GuidelineSegment { text: string; ruleIds: string[] }

/**
 * Split guideline text into runs so each exact citation is highlighted where it appears,
 * even when it is only part of a line or several rules cite overlapping text.
 */
export function segmentGuideline(text: string, rules: Pick<Rule, 'rule_id' | 'source_sentence'>[]): GuidelineSegment[] {
  if (!text) return [];
  const owners: string[][] = Array.from({ length: text.length }, () => []);
  for (const rule of rules) {
    const citation = rule.source_sentence;
    if (!citation) continue;
    let from = 0;
    for (;;) {
      const index = text.indexOf(citation, from);
      if (index < 0) break;
      for (let i = index; i < index + citation.length; i += 1) owners[i]?.push(rule.rule_id);
      from = index + Math.max(1, citation.length);
    }
  }
  const segments: GuidelineSegment[] = [];
  let start = 0;
  const key = (i: number) => (owners[i] ?? []).join('\u0000');
  for (let i = 1; i <= text.length; i += 1) {
    if (i === text.length || key(i) !== key(start)) {
      segments.push({ text: text.slice(start, i), ruleIds: [...(owners[start] ?? [])] });
      start = i;
    }
  }
  return segments;
}

/** Next unused rule ID in the `RULE-n` series. */
export function suggestRuleId(rules: Pick<Rule, 'rule_id'>[], prefix = 'RULE'): string {
  const used = new Set(rules.map((rule) => rule.rule_id));
  let n = rules.length + 1;
  while (used.has(`${prefix}-${n}`)) n += 1;
  return `${prefix}-${n}`;
}

export type ParseResult = { ok: true; value: Record<string, unknown> } | { ok: false; error: string };

/** Parameters must be a JSON object, e.g. {"max_iou": 0.3}. */
export function parseParameters(text: string): ParseResult {
  if (!text.trim()) return { ok: true, value: {} };
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ok: false, error: 'Parameters must be a JSON object, e.g. {"max_iou": 0.3}.' };
    return { ok: true, value: value as Record<string, unknown> };
  } catch (error) {
    return { ok: false, error: `Parameters are not valid JSON: ${error instanceof Error ? error.message : String(error)}` };
  }
}

/** Starter parameters for each executable check so reviewers do not need to know the schema. */
export const CHECK_PARAMETER_TEMPLATES: Record<string, Record<string, unknown>> = {
  allowed_labels: { allowed_labels: ['person'] },
  box_overlaps: { max_iou: 0.3 },
  box_mask_alignment: { max_edge_difference: 5 },
  mask_outside_box: { max_outside_fraction: 0.05 },
  mask_box_fill: { min_fill_fraction: 0.3, max_fill_fraction: 1 },
  mask_components: { max_component_count: 1 },
  person_coverage: { min_tolerance_px: 8, tolerance_fraction: 0.04 },
  unannotated_people: { min_person_height_px: 40, min_score: 0.9 },
  polygon_validity: {},
  annotation_guideline: {},
  annotation_ids: {},
  annotation_metadata: {},
  box_validity: {},
  mask_dimensions: {},
  mask_ids: {},
};

export interface RuleDraftInput {
  rule_id: string;
  description: string;
  check_name: string;
  parameters: string;
  source_sentence: string;
}

/** Validate a reviewer's add/edit form and build the rule, or return the problems to show. */
export function buildRule(
  input: RuleDraftInput,
  context: { guidelineText: string; otherRuleIds: string[]; origin: Rule['origin']; confirmationState: Rule['confirmation_state'] },
): { rule: Rule; errors: [] } | { rule: null; errors: string[] } {
  const errors: string[] = [];
  const ruleId = input.rule_id.trim();
  const description = input.description.trim();
  const citation = input.source_sentence.trim();
  if (!ruleId) errors.push('Rule ID is required.');
  else if (context.otherRuleIds.includes(ruleId)) errors.push(`Rule ID ${ruleId} is already used.`);
  if (!description) errors.push('Describe what the rule requires.');
  const parsed = parseParameters(input.parameters);
  if (!parsed.ok) errors.push(parsed.error);
  if (citation && !context.guidelineText.includes(citation)) errors.push('The citation must be copied exactly from the guideline text.');
  if (context.origin === 'extracted' && !citation) errors.push('An extracted rule must keep its guideline citation.');
  if (errors.length || !parsed.ok) return { rule: null, errors };
  return {
    rule: {
      rule_id: ruleId,
      description,
      check_name: input.check_name.trim() || null,
      parameters: parsed.value as Rule['parameters'],
      origin: context.origin,
      source_sentence: citation || null,
      confirmation_state: context.confirmationState,
    },
    errors: [],
  };
}

/** Rules whose citation no longer appears in edited guideline text (the backend would reject them). */
export function rulesWithMissingCitations(rules: Rule[], guidelineText: string): Rule[] {
  return rules.filter((rule) => rule.source_sentence && !guidelineText.includes(rule.source_sentence));
}

/** What still blocks confirmation: undecided rules, and rejected rules that will be dropped. */
export function confirmationBlockers(rules: Rule[]): { undecided: Rule[]; rejected: Rule[]; retained: Rule[] } {
  return {
    undecided: rules.filter((rule) => (rule.confirmation_state ?? 'draft') === 'draft'),
    rejected: rules.filter((rule) => rule.confirmation_state === 'rejected'),
    retained: rules.filter((rule) => rule.confirmation_state !== 'rejected'),
  };
}

const ruleSignature = (rule: Pick<Rule, 'check_name' | 'parameters' | 'source_sentence'>) =>
  JSON.stringify([rule.source_sentence ?? '', rule.check_name ?? '', sortKeys(rule.parameters ?? {})]);

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, sortKeys(v)]));
  }
  return value;
}

/**
 * Proposals from a re-run extraction that are not already in the draft.
 * Colliding IDs are renamed so nothing in the current draft is overwritten.
 */
export function newProposals(current: Rule[], extracted: Rule[]): Rule[] {
  const existing = new Set(current.map(ruleSignature));
  const ids = new Set(current.map((rule) => rule.rule_id));
  const result: Rule[] = [];
  for (const proposal of extracted) {
    if (existing.has(ruleSignature(proposal))) continue;
    let ruleId = proposal.rule_id;
    let n = 2;
    while (ids.has(ruleId)) { ruleId = `${proposal.rule_id}-${n}`; n += 1; }
    ids.add(ruleId);
    existing.add(ruleSignature(proposal));
    result.push({ ...proposal, rule_id: ruleId, confirmation_state: 'draft' });
  }
  return result;
}

/** Adopting a proposal into another book is an explicit reviewer addition. */
export function adoptProposal(proposal: Rule): Rule {
  return { ...proposal, origin: 'reviewer', confirmation_state: 'draft' };
}

/** Retained IDs keep their existing origin; new additions use reviewer origin. */
export function rulesForDraftSave(previous: Rule[], edited: Rule[]): Rule[] {
  const retained = new Set(previous.map((rule) => rule.rule_id));
  return edited.map((rule) => retained.has(rule.rule_id) ? rule : { ...rule, origin: 'reviewer' });
}
