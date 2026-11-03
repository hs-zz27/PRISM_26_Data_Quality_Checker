/**
 * Readable names for audits and rulebooks.
 *
 * The backend allocates permanent labels ("Audit 014", "Audit 014 R01",
 * "Rules 003") and editable titles. Every screen formats them through these
 * helpers so the format never diverges. Labels are display text only: requests,
 * routes and cache keys always use internal IDs, and nothing here parses a
 * label back into an ID.
 */
import type { components } from '../api/generated/openapi';

export type AuditName = components['schemas']['AuditName'];
export type RulebookName = components['schemas']['RulebookName'];
type Maybe<T> = T | null | undefined;

/** Shown when a record has no name (for example, before the database migration ran). */
export const UNNAMED_AUDIT = 'Unnumbered audit';
export const UNNAMED_RULEBOOK = 'Unnumbered rules';

function cleanTitle(title: Maybe<string>): string | null {
  const trimmed = title?.trim();
  return trimmed ? trimmed : null;
}

/** "Audit 014", or "Audit 014 R01" for a recheck. */
export function auditLabel(name: Maybe<AuditName>): string {
  return name?.label ?? UNNAMED_AUDIT;
}

export function auditTitle(name: Maybe<AuditName>): string | null {
  return cleanTitle(name?.title);
}

/** "Audit 014 | LabelMe demo": the label, then the title when there is one. */
export function auditHeading(name: Maybe<AuditName>): string {
  const title = auditTitle(name);
  return title ? `${auditLabel(name)} | ${title}` : auditLabel(name);
}

export function isRecheck(name: Maybe<AuditName>): boolean {
  return name?.recheck_number != null;
}

/** "Rules 003 v3". Show the version whenever it is known: audits pin an exact version. */
export function rulebookLabel(name: Maybe<RulebookName>, version?: number | null): string {
  const base = name?.label ?? UNNAMED_RULEBOOK;
  return version != null ? `${base} v${version}` : base;
}

export function rulebookTitle(name: Maybe<RulebookName>): string | null {
  return cleanTitle(name?.title);
}

/** "Rules 003 v3 | Person annotation checks". */
export function rulebookHeading(name: Maybe<RulebookName>, version?: number | null): string {
  const title = rulebookTitle(name);
  return title ? `${rulebookLabel(name, version)} | ${title}` : rulebookLabel(name, version);
}

/** A file-name-safe form of a label, e.g. "audit-014-r01". Never used to look anything up. */
export function labelSlug(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'record';
}
