import React, { useEffect } from 'react';
import { ApiError } from '../../api/client';
import { useJobStore } from '../../store/jobStore';
import { useJobPolling } from '../../hooks/useJobPolling';
import { useRestoreActiveJobs } from '../../hooks/useRestoreActiveJobs';
import { useDisplayNames } from '../../hooks/useDisplayNames';
import { auditLabel, rulebookLabel } from '../../lib/names';

const KIND_LABEL: Record<string, string> = {
  rule_extraction: 'Guideline extraction', dataset_import: 'Dataset import', audit: 'Audit',
  recheck: 'Recheck', export: 'Export', feedback_evaluation: 'Policy evaluation',
};

function JobItem({ id }: { id: string }) {
  const state = useJobPolling(id);
  const remove = useJobStore((s) => s.remove);
  const result = state.result;
  // A finished job names what it produced ("Audit 014 R01", "Rules 003 v1"); the job ID stays in the tooltip.
  const names = useDisplayNames({
    auditIds: result?.kind === 'audit' ? [result.id] : [],
    rulebookIds: result?.kind === 'rulebook' ? [result.id] : [],
  });
  const produced = result?.kind === 'audit' ? auditLabel(names.audit(result.id))
    : result?.kind === 'rulebook' ? rulebookLabel(names.rulebook(result.id), result.version) : null;
  const kind = state.job ? KIND_LABEL[state.job.kind] ?? state.job.kind : 'Job';
  // A remembered job the backend no longer knows is dropped instead of shown forever.
  useEffect(() => { if (state.error instanceof ApiError && state.error.status === 404) remove(id); }, [state.error, id, remove]);
  return <div className="text-2xs px-2 py-1 border-l border-border whitespace-nowrap" title={`Job ID ${id}`}>
    {kind}{produced && !names.isLoading ? ` · ${produced}` : ''} · {state.status ?? (state.isLoading ? 'loading' : 'unavailable')}
    {state.isActive && <button className="ml-2" onClick={() => state.cancel()}>Cancel</button>}
  </div>;
}
export const GlobalJobTray: React.FC = () => { useRestoreActiveJobs(); const ids = useJobStore((state) => state.ids); return ids.length ? <div className="flex items-center overflow-x-auto border-t border-border bg-surface px-2">{ids.map((id) => <JobItem key={id} id={id} />)}</div> : null; };
