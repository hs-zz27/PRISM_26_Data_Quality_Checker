import React, { useState } from 'react';
import { Button } from '../../components/ui/Button';
import { checkLabel } from '../../lib/labels';
import { buildRule, CHECK_PARAMETER_TEMPLATES, type RuleDraftInput } from './rulebookEditing';
import type { components } from '../../api/generated/openapi';

type Rule = components['schemas']['Rule'];

interface RuleFormProps {
  initial?: Rule;
  suggestedId: string;
  guidelineText: string;
  otherRuleIds: string[];
  /** Text the reviewer highlighted in the guideline pane, offered as the citation. */
  selectedText: string;
  checks: { name: string; implementation: string }[];
  onSave: (rule: Rule) => void;
  onCancel: () => void;
}

/** Add a missed rule or edit an existing one. Reviewer-added rules join the draft as confirmed. */
export const RuleForm: React.FC<RuleFormProps> = ({ initial, suggestedId, guidelineText, otherRuleIds, selectedText, checks, onSave, onCancel }) => {
  const [input, setInput] = useState<RuleDraftInput>(() => ({
    rule_id: initial?.rule_id ?? suggestedId,
    description: initial?.description ?? '',
    check_name: initial?.check_name ?? '',
    parameters: initial ? JSON.stringify(initial.parameters ?? {}, null, 2) : '{}',
    source_sentence: initial?.source_sentence ?? (selectedText && guidelineText.includes(selectedText) ? selectedText : ''),
  }));
  const [errors, setErrors] = useState<string[]>([]);
  const update = (field: keyof RuleDraftInput, value: string) => setInput((old) => ({ ...old, [field]: value }));
  const knownCheck = !input.check_name || checks.some((check) => check.name === input.check_name);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const result = buildRule(input, {
      guidelineText,
      otherRuleIds,
      origin: initial?.origin ?? 'reviewer',
      confirmationState: initial ? initial.confirmation_state ?? 'draft' : 'confirmed',
    });
    if (result.rule) onSave(result.rule);
    else setErrors(result.errors);
  };

  return (
    <form className="rule-form" onSubmit={submit} onClick={(event) => event.stopPropagation()} aria-label={initial ? `Edit rule ${initial.rule_id}` : 'Add a missed rule'}>
      <div className="rule-form-grid">
        <label>Rule ID<input value={input.rule_id} onChange={(e) => update('rule_id', e.target.value)} disabled={Boolean(initial)} /></label>
        <label>Automatic check
          <select
            value={input.check_name}
            onChange={(e) => {
              const name = e.target.value;
              update('check_name', name);
              // Prefill parameters when the reviewer has not typed any yet.
              if (name && (!input.parameters.trim() || input.parameters.trim() === '{}')) update('parameters', JSON.stringify(CHECK_PARAMETER_TEMPLATES[name] ?? {}, null, 2));
            }}
          >
            <option value="">None — reviewer checks by eye</option>
            {checks.map((check) => (
              <option key={check.name} value={check.name}>{checkLabel(check.name)}{check.implementation !== 'implemented' ? ' (planned)' : ''}</option>
            ))}
            {!knownCheck && <option value={input.check_name}>{input.check_name} (not in catalogue)</option>}
          </select>
        </label>
      </div>
      <label>What the rule requires<textarea rows={2} value={input.description} onChange={(e) => update('description', e.target.value)} placeholder="e.g. Every person must have exactly one box." /></label>
      <label>Parameters (JSON)<textarea rows={3} className="font-mono" value={input.parameters} onChange={(e) => update('parameters', e.target.value)} /></label>
      <label>Guideline citation (exact text)
        <textarea rows={2} value={input.source_sentence} onChange={(e) => update('source_sentence', e.target.value)} placeholder="Highlight a sentence in the guideline on the left, or leave empty for a reviewer-only rule." />
      </label>
      {selectedText && selectedText !== input.source_sentence && guidelineText.includes(selectedText) && (
        <button type="button" className="rule-form-use-selection" onClick={() => update('source_sentence', selectedText)}>Use highlighted text: “{selectedText.slice(0, 80)}{selectedText.length > 80 ? '…' : ''}”</button>
      )}
      {errors.length > 0 && <ul role="alert" className="rule-form-errors">{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
      <div className="rule-form-actions">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        <Button type="submit" size="sm" variant="primary">{initial ? 'Apply changes' : 'Add rule'}</Button>
      </div>
    </form>
  );
};
