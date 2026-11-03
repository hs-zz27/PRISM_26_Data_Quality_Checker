import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiGet } from '../api/client';
import { queryKeys } from '../api/queryClient';
import type { AuditName, RulebookName } from '../lib/names';

const LOOKUP_LIMIT = 100;

function uniqueIds(ids: readonly (string | null | undefined)[] | undefined): string[] {
  return [...new Set((ids ?? []).filter((id): id is string => Boolean(id && id.trim())))].sort();
}

function chunks(ids: string[]): string[][] {
  const result: string[][] = [];
  for (let start = 0; start < ids.length; start += LOOKUP_LIMIT) result.push(ids.slice(start, start + LOOKUP_LIMIT));
  return result;
}

export interface DisplayNames {
  audit: (auditId: string | null | undefined) => AuditName | undefined;
  rulebook: (rulebookId: string | null | undefined) => RulebookName | undefined;
  isLoading: boolean;
}

/**
 * Resolve names for audit and rulebook IDs held by other records (findings,
 * revisions, exports, attempts). Audit and rulebook responses already carry
 * their own names; use this only for bare references. Unknown IDs resolve to
 * undefined, and screens show the shared fallback label.
 */
export function useDisplayNames({ auditIds, rulebookIds }: {
  auditIds?: readonly (string | null | undefined)[];
  rulebookIds?: readonly (string | null | undefined)[];
}): DisplayNames {
  const audits = uniqueIds(auditIds);
  const rulebooks = uniqueIds(rulebookIds);
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.displayNameLookup(audits, rulebooks),
    enabled: audits.length + rulebooks.length > 0,
    queryFn: async () => {
      const auditPages = chunks(audits);
      const bookPages = chunks(rulebooks);
      const pages = await Promise.all(Array.from({ length: Math.max(auditPages.length, bookPages.length) }, (_, index) =>
        apiGet('/display-names', { query: { audit_id: auditPages[index] ?? [], rulebook_id: bookPages[index] ?? [] } })));
      const auditNames = new Map<string, AuditName>();
      const rulebookNames = new Map<string, RulebookName>();
      for (const page of pages) {
        for (const name of page.audits ?? []) auditNames.set(name.audit_id, name);
        for (const name of page.rulebooks ?? []) rulebookNames.set(name.rulebook_id, name);
      }
      return { audits: auditNames, rulebooks: rulebookNames };
    },
  });
  return useMemo(() => ({
    audit: (id) => (id ? data?.audits.get(id) : undefined),
    rulebook: (id) => (id ? data?.rulebooks.get(id) : undefined),
    isLoading: isLoading && audits.length + rulebooks.length > 0,
  }), [data, isLoading, audits.length, rulebooks.length]);
}
