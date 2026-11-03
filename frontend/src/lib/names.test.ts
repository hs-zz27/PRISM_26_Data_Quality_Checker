import { describe, expect, it } from 'vitest';
import {
  UNNAMED_AUDIT, UNNAMED_RULEBOOK, auditHeading, auditLabel, auditTitle, isRecheck, labelSlug,
  rulebookHeading, rulebookLabel, rulebookTitle,
} from './names';
import { makeAuditName, makeRulebookName } from '../test/fixtures';

describe('audit names', () => {
  it('shows the server label, then the title', () => {
    const name = makeAuditName();
    expect(auditLabel(name)).toBe('Audit 014');
    expect(auditTitle(name)).toBe('LabelMe demo');
    expect(auditHeading(name)).toBe('Audit 014 | LabelMe demo');
    expect(isRecheck(name)).toBe(false);
  });

  it('keeps the root number visible for rechecks, including past 99', () => {
    const recheck = makeAuditName({ audit_id: 'child', recheck_number: 1, label: 'Audit 014 R01', title: 'Feedback recheck' });
    expect(auditHeading(recheck)).toBe('Audit 014 R01 | Feedback recheck');
    expect(isRecheck(recheck)).toBe(true);
    expect(auditLabel(makeAuditName({ number: 1000, recheck_number: 100, label: 'Audit 1000 R100' }))).toBe('Audit 1000 R100');
  });

  it('uses one fallback when a record has no name, and drops blank titles', () => {
    expect(auditLabel(null)).toBe(UNNAMED_AUDIT);
    expect(auditLabel(undefined)).toBe(UNNAMED_AUDIT);
    expect(auditHeading(undefined)).toBe(UNNAMED_AUDIT);
    expect(auditHeading(makeAuditName({ title: '   ' }))).toBe('Audit 014');
    expect(auditHeading(makeAuditName({ title: '  Spaced  ' }))).toBe('Audit 014 | Spaced');
  });
});

describe('rulebook names', () => {
  it('shares one label across versions and always shows the exact version when known', () => {
    const name = makeRulebookName();
    expect(rulebookLabel(name)).toBe('Rules 003');
    expect(rulebookLabel(name, 1)).toBe('Rules 003 v1');
    expect(rulebookLabel(name, 3)).toBe('Rules 003 v3');
    expect(rulebookHeading(name, 3)).toBe('Rules 003 v3 | Person annotation checks');
    expect(rulebookTitle(makeRulebookName({ title: '' }))).toBeNull();
  });

  it('falls back without losing the version', () => {
    expect(rulebookLabel(null, 2)).toBe(`${UNNAMED_RULEBOOK} v2`);
    expect(rulebookHeading(undefined)).toBe(UNNAMED_RULEBOOK);
  });
});

describe('labelSlug', () => {
  it('makes file-name-safe text from a label', () => {
    expect(labelSlug('Audit 014 R01')).toBe('audit-014-r01');
    expect(labelSlug('Rules 1000 v12')).toBe('rules-1000-v12');
    expect(labelSlug('***')).toBe('record');
  });
});
