import { describe, expect, it } from 'vitest';
import { buildRule, confirmationBlockers, newProposals, parseParameters, rulesWithMissingCitations, segmentGuideline, suggestRuleId } from './rulebookEditing';
import { makeRule } from '../../test/fixtures';

const TEXT = 'Boxes must be tight. Allowed labels are person. Masks must be one piece.';

describe('guideline citation highlighting', () => {
  it('highlights exact citations inside a line and keeps all text', () => {
    const segments = segmentGuideline(TEXT, [makeRule({ rule_id: 'L', source_sentence: 'Allowed labels are person.' })]);
    expect(segments.map((s) => s.text).join('')).toBe(TEXT);
    expect(segments.find((s) => s.ruleIds.length)).toEqual({ text: 'Allowed labels are person.', ruleIds: ['L'] });
  });

  it('marks overlapping citations with every rule that cites them', () => {
    const segments = segmentGuideline(TEXT, [
      makeRule({ rule_id: 'A', source_sentence: 'Boxes must be tight.' }),
      makeRule({ rule_id: 'B', source_sentence: 'tight. Allowed' }),
    ]);
    expect(segments.find((s) => s.text === 'tight.')?.ruleIds).toEqual(['A', 'B']);
    expect(segments.map((s) => s.text).join('')).toBe(TEXT);
  });

  it('ignores reviewer rules without a citation', () => {
    expect(segmentGuideline('abc', [makeRule({ source_sentence: null })])).toEqual([{ text: 'abc', ruleIds: [] }]);
    expect(segmentGuideline('', [])).toEqual([]);
  });
});

describe('rule form', () => {
  const context = { guidelineText: TEXT, otherRuleIds: ['LBL-1'], origin: 'reviewer' as const, confirmationState: 'confirmed' as const };
  const input = { rule_id: 'RULE-2', description: 'One mask piece', check_name: 'mask_components', parameters: '{"max_component_count": 1}', source_sentence: 'Masks must be one piece.' };

  it('builds a reviewer rule from valid input', () => {
    const result = buildRule(input, context);
    expect(result.errors).toEqual([]);
    expect(result.rule).toMatchObject({ rule_id: 'RULE-2', origin: 'reviewer', confirmation_state: 'confirmed', parameters: { max_component_count: 1 }, source_sentence: 'Masks must be one piece.' });
  });

  it('allows a reviewer rule without a citation', () => {
    expect(buildRule({ ...input, source_sentence: '  ' }, context).rule?.source_sentence).toBeNull();
  });

  it('reports duplicate IDs, bad JSON and citations not in the text', () => {
    const result = buildRule({ ...input, rule_id: 'LBL-1', parameters: '[1]', source_sentence: 'Not in the guideline.' }, context);
    expect(result.rule).toBeNull();
    expect(result.errors).toHaveLength(3);
  });

  it('keeps extracted rules tied to a citation', () => {
    expect(buildRule({ ...input, source_sentence: '' }, { ...context, origin: 'extracted' }).errors).toContain('An extracted rule must keep its guideline citation.');
  });

  it('parses parameters as a JSON object', () => {
    expect(parseParameters('')).toEqual({ ok: true, value: {} });
    expect(parseParameters('{"a":1}')).toEqual({ ok: true, value: { a: 1 } });
    expect(parseParameters('nope').ok).toBe(false);
  });

  it('suggests an unused rule ID', () => {
    expect(suggestRuleId([{ rule_id: 'RULE-2' }, { rule_id: 'RULE-3' }])).toBe('RULE-4');
    expect(suggestRuleId([])).toBe('RULE-1');
  });
});

describe('confirmation', () => {
  it('separates undecided, rejected and retained rules', () => {
    const rules = [makeRule({ rule_id: 'A' }), makeRule({ rule_id: 'B', confirmation_state: 'draft' }), makeRule({ rule_id: 'C', confirmation_state: 'rejected' })];
    const blockers = confirmationBlockers(rules);
    expect(blockers.undecided.map((r) => r.rule_id)).toEqual(['B']);
    expect(blockers.rejected.map((r) => r.rule_id)).toEqual(['C']);
    expect(blockers.retained.map((r) => r.rule_id)).toEqual(['A', 'B']);
  });

  it('flags rules whose citation was removed from the text', () => {
    expect(rulesWithMissingCitations([makeRule({ source_sentence: 'gone' }), makeRule({ rule_id: 'X', source_sentence: null })], TEXT).map((r) => r.rule_id)).toEqual(['LBL-1']);
  });
});

describe('re-check proposals', () => {
  it('drops proposals already in the draft and renames colliding IDs', () => {
    const current = [makeRule({ rule_id: 'R1' })];
    const extracted = [
      makeRule({ rule_id: 'R9', parameters: { allowed_labels: ['person'] } }),
      makeRule({ rule_id: 'R1', check_name: 'mask_components', parameters: { max_component_count: 1 }, source_sentence: 'Masks must be one piece.' }),
    ];
    const proposals = newProposals(current, extracted);
    expect(proposals).toHaveLength(1);
    expect(proposals[0]).toMatchObject({ rule_id: 'R1-2', check_name: 'mask_components', confirmation_state: 'draft' });
  });
});
