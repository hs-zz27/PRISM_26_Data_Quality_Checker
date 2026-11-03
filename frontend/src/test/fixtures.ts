import type { components } from '../api/generated/openapi';

type Finding = components['schemas']['Finding'];
type SampleAudit = components['schemas']['SampleAudit'];
type SampleAuditSummary = components['schemas']['SampleAuditSummary'];
type Rule = components['schemas']['Rule'];
type AuditName = components['schemas']['AuditName'];
type RulebookName = components['schemas']['RulebookName'];

export function makeAuditName(overrides: Partial<AuditName> = {}): AuditName {
  return {
    audit_id: 'audit-1', root_audit_id: 'audit-1', number: 14, recheck_number: null, label: 'Audit 014',
    title: 'LabelMe demo', title_revision: 0, title_updated_at: null, title_updated_by: null,
    ...overrides,
  };
}

export function makeRulebookName(overrides: Partial<RulebookName> = {}): RulebookName {
  return {
    rulebook_id: 'book-1', number: 3, label: 'Rules 003', title: 'Person annotation checks',
    title_revision: 0, title_updated_at: null, title_updated_by: null,
    ...overrides,
  };
}

export function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'finding-1',
    audit_id: 'audit-1',
    sample_id: 'sample-1',
    sample_revision: 1,
    object_id: 1,
    rulebook_id: 'book-1',
    rulebook_version: 2,
    rule_id: 'LBL-1',
    description: 'Use an allowed label.',
    check_name: 'allowed_labels',
    outcome: 'fail',
    reason: 'label_not_allowed',
    parameters: { allowed_labels: ['person'] },
    origin: 'extracted',
    source_sentence: 'Allowed labels are person.',
    evidence: { image_url: null, mask_preview_url: null, crop_url: null, measurements: { actual_label: 'vehicle' } },
    review: { revision: 0, resolution: 'pending', reviewer_id: null, comment: null, updated_at: null },
    ...overrides,
  };
}

export function makeSummary(overrides: Partial<SampleAuditSummary> = {}): SampleAuditSummary {
  return {
    sample_id: 'sample-1',
    sample_revision: 1,
    processing_status: 'completed',
    incomplete: false,
    tool_status: 'flagged',
    review_status: 'pending_review',
    finding_counts: { pass: 0, fail: 1, unsure: 0, unavailable: 0 },
    thumbnail_url: null,
    result_audit_id: 'audit-1',
    ...overrides,
  };
}

export function makeSampleAudit(findings: Finding[], overrides: Partial<SampleAudit> = {}): SampleAudit {
  const summary = makeSummary({ sample_id: findings[0]?.sample_id ?? 'sample-1', ...(overrides.summary ?? {}) });
  return {
    audit_id: 'audit-1',
    dataset_id: 'dataset-1',
    workspace_audit_id: 'audit-1',
    attempts: [],
    measurements: {},
    failures: [],
    review: { audit_id: 'audit-1', sample_id: summary.sample_id, sample_revision: summary.sample_revision, revision: 0, decision: 'pending_review', reviewer_id: null, comment: null, updated_at: null },
    ...overrides,
    summary,
    findings,
  };
}

export function makeRule(overrides: Partial<Rule> = {}): Rule {
  return {
    rule_id: 'LBL-1',
    description: 'Use an allowed label.',
    check_name: 'allowed_labels',
    parameters: { allowed_labels: ['person'] },
    origin: 'extracted',
    source_sentence: 'Allowed labels are person.',
    confirmation_state: 'confirmed',
    ...overrides,
  };
}
