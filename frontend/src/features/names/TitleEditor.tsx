import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Pencil } from 'lucide-react';
import { apiPut, ApiError, isVersionConflict } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Button } from '../../components/ui/Button';
import { useReviewerStore } from '../../store/reviewerStore';
import type { AuditName, RulebookName } from '../../lib/names';

export const TITLE_MAX_LENGTH = 120;

type Target =
  | { kind: 'audit'; id: string; name: AuditName | null | undefined }
  | { kind: 'rulebook'; id: string; name: RulebookName | null | undefined };

/**
 * Rename an audit or rulebook. Only the title changes: the permanent label,
 * versions, findings and corrections are untouched. Uses the title revision
 * last read, so a concurrent rename is reported instead of overwritten.
 */
export function TitleEditor(target: Target) {
  const queryClient = useQueryClient();
  const { getActiveId, openPrompt } = useReviewerStore();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const name = target.name;

  const save = useMutation({
    mutationFn: async (title: string) => {
      const reviewerId = getActiveId();
      if (!reviewerId) {
        openPrompt();
        throw new Error('Set your reviewer name first (top right).');
      }
      const body = { title, expected_title_revision: name!.title_revision, reviewer_id: reviewerId };
      return target.kind === 'audit'
        ? apiPut('/audits/{audit_id}/title', { path: { audit_id: target.id }, body })
        : apiPut('/rulebooks/{rulebook_id}/title', { path: { rulebook_id: target.id }, body });
    },
    onSuccess: () => {
      setEditing(false);
      setError(null);
    },
    onError: (err) => {
      setError(isVersionConflict(err)
        ? 'Someone else renamed this since you opened it. The latest title is shown; edit it again if needed.'
        : err instanceof ApiError || err instanceof Error ? err.message : String(err));
    },
    onSettled: () => {
      // Refresh every view of the record and any bare references to it.
      void queryClient.invalidateQueries({ queryKey: target.kind === 'audit' ? queryKeys.audits() : queryKeys.rulebooks() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.displayNames() });
    },
  });

  if (!name) return null;
  const trimmed = draft.trim();
  const invalid = !trimmed || trimmed.length > TITLE_MAX_LENGTH;

  if (!editing) {
    return <button type="button" className="inline-flex items-center gap-1 text-2xs text-text-muted hover:text-text"
      onClick={() => { setDraft(name.title); setError(null); setEditing(true); }}
      aria-label={`Rename ${name.label}`}>
      <Pencil size={11} /> Rename
    </button>;
  }
  return <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => {
    event.preventDefault();
    if (!invalid && trimmed !== name.title) save.mutate(trimmed); else if (trimmed === name.title) setEditing(false);
  }}>
    <label className="sr-only" htmlFor={`title-${target.kind}-${target.id}`}>Title for {name.label}</label>
    <input id={`title-${target.kind}-${target.id}`} className="min-w-0 flex-1 text-sm" value={draft} maxLength={TITLE_MAX_LENGTH}
      onChange={(event) => setDraft(event.target.value)} autoFocus aria-invalid={invalid} />
    <Button type="submit" size="sm" variant="primary" disabled={invalid} isLoading={save.isPending}>Save</Button>
    <Button type="button" size="sm" variant="ghost" onClick={() => { setEditing(false); setError(null); }}>Cancel</Button>
    <span className="text-2xs text-text-muted">{name.label} stays the same; only the title changes.</span>
    {error && <p role="alert" className="w-full text-2xs text-status-fail">{error}</p>}
  </form>;
}
