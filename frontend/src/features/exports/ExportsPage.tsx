import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RefreshCw } from 'lucide-react';
import { ApiError, apiGet, apiGetAllPages, apiPost, buildApiUrl } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { useJobPolling } from '../../hooks/useJobPolling';
import { exclusionReasonLabel, jobStatusLabel, reviewStatusLabel } from '../../lib/labels';
import { formatTimestamp } from '../../lib/format';
import type { components } from '../../api/generated/openapi';
import { getExportDetailPollInterval, getTrackedExportJob } from './exportPolling';

type ExportRecord = components['schemas']['Export'];
type ExportSelection = components['schemas']['ExportSelection'];

const SELECTION_TEXT: Record<ExportSelection, { title: string; detail: string }> = {
  accepted_only: { title: 'Accepted samples only', detail: 'Training-ready set: only samples a reviewer explicitly accepted.' },
  all_reviewed: { title: 'Accepted and rejected', detail: 'Diagnostic set: rejected samples stay marked rejected. Not for training.' },
};

function ExportHistoryRow({ record }: { record: ExportRecord }) {
  const [expanded, setExpanded] = useState(false);
  const activeJobId = getTrackedExportJob(record);
  const job = useJobPolling(activeJobId);
  const detail = useQuery({
    queryKey: queryKeys.export(record.export_id),
    queryFn: () => apiGet('/exports/{export_id}', { path: { export_id: record.export_id } }),
    enabled: expanded || Boolean(activeJobId),
    refetchInterval: getExportDetailPollInterval(record),
  });
  const current = detail.data ?? record;
  const manifest = useQuery({
    queryKey: ['exports', record.export_id, 'manifest'],
    queryFn: () => apiGet('/exports/{export_id}/manifest', { path: { export_id: record.export_id } }),
    enabled: expanded && current.status === 'completed',
  });
  const status = job.status ?? current.status;
  return (
    <article className="export-row">
      <div className="export-row-main">
        <span className="font-mono text-text">{record.export_id}</span>
        <Badge size="sm" variant={status === 'completed' ? 'pass' : status === 'failed' || status === 'cancelled' ? 'fail' : 'pending'}>{jobStatusLabel(status)}</Badge>
        <span className="text-text-muted">audit <span className="font-mono">{record.audit_id}</span> · {SELECTION_TEXT[record.selection].title}{record.include_images ? ' · with images' : ''}</span>
        {current.included_count !== null && current.included_count !== undefined && (
          <span className="text-text-muted">{current.included_count} included · {current.excluded_count ?? 0} excluded</span>
        )}
        <span className="text-text-faint text-2xs">{formatTimestamp(record.created_at)}</span>
        <span className="export-row-actions">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((open) => !open)}>{expanded ? 'Hide manifest' : 'Manifest'}</Button>
          {current.status === 'completed'
            ? <a className="review-header-link" href={buildApiUrl('/exports/{export_id}/download', { export_id: record.export_id })}><Download size={12} /> Download ZIP</a>
            : <span className="text-text-faint text-2xs">Download available when complete</span>}
        </span>
      </div>
      {detail.error && <p role="alert" className="text-xs text-status-fail">Export detail: {detail.error.message}</p>}
      {expanded && current.status !== 'completed' && <p className="text-xs text-text-muted">The manifest is available once the export completes.</p>}
      {manifest.error && <p role="alert" className="text-xs text-status-fail">Manifest: {manifest.error.message}</p>}
      {expanded && manifest.data && (
        <div className="export-manifest">
          <p className="text-2xs text-text-muted">
            Dataset <span className="font-mono">{manifest.data.dataset_id}</span> · rulebook <span className="font-mono">{manifest.data.rulebook_id} v{manifest.data.rulebook_version}</span> · decisions as of {formatTimestamp(manifest.data.selected_at)}
          </p>
          <table className="report-table">
            <thead><tr><th>Sample</th><th>Included</th><th>Decision</th><th>Reviewer</th><th>Why excluded</th></tr></thead>
            <tbody>
              {manifest.data.entries.map((entry) => (
                <tr key={`${entry.sample_id}-${entry.ordinal}`}>
                  <td className="font-mono">{entry.sample_id} <small>r{entry.sample_revision}</small></td>
                  <td>{entry.included ? <span className="text-status-pass">Yes</span> : <span className="text-text-faint">No</span>}</td>
                  <td>{reviewStatusLabel(entry.review_decision)}</td>
                  <td>{entry.reviewer_id ?? '—'}{entry.reviewed_at ? <small> · {formatTimestamp(entry.reviewed_at)}</small> : null}</td>
                  <td>{entry.reason ? exclusionReasonLabel(entry.reason) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

export const ExportsPage: React.FC = () => {
  const client = useQueryClient();
  const [auditId, setAuditId] = useState('');
  const [selection, setSelection] = useState<ExportSelection>('accepted_only');
  const [includeImages, setIncludeImages] = useState(true);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const history = useQuery({
    queryKey: queryKeys.exports(),
    queryFn: () => apiGetAllPages('/exports', { query: { limit: 100, offset: 0 } }),
  });
  const audits = useQuery({ queryKey: queryKeys.auditWorkspaces(), queryFn: () => apiGetAllPages('/audits', { query: { limit: 100, offset: 0, roots_only: true } }) });
  // Only finished audits can be exported; running ones return AUDIT_NOT_READY.
  const exportableAudits = useMemo(() => (audits.data?.items ?? []).filter((audit) => audit.status === 'completed'), [audits.data]);

  const datasets = useQuery({ queryKey: queryKeys.datasets(), queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }) });
  const selectedDatasetId = exportableAudits.find((audit) => audit.audit_id === auditId)?.dataset_id;
  const exportFormat = datasets.data?.items.find((dataset) => dataset.dataset_id === selectedDatasetId)?.format;

  const start = useMutation({
    mutationFn: () => { if (!exportFormat) throw new Error('The source dataset format is unavailable.'); return apiPost<'/exports'>('/exports', { body: { audit_id: auditId, format: exportFormat, selection, include_images: includeImages, include_rechecks: true } }); },
    onSuccess: (result) => {
      setMessage({ tone: 'ok', text: `Export ${result.export.export_id} queued. It appears below and updates automatically.` });
      void client.invalidateQueries({ queryKey: queryKeys.exports() });
    },
    onError: (error) => setMessage({
      tone: 'error',
      text: error instanceof ApiError && error.code === 'AUDIT_NOT_READY' ? 'That audit or one of its rechecks is still running. Wait for it to finish, then export.'
        : error instanceof ApiError && error.code === 'AUDIT_NOT_EXPORTABLE' ? 'This audit was created in a legacy mode that cannot be exported reproducibly.'
          : error.message,
    }),
  });

  return (
    <div className="workstation-page exports-page space-y-5">
      <header>
        <h1>Export reviewed data</h1>
        <p className="text-xs text-text-muted mt-1">Package the latest reviewed attempt for each image, including rechecks. Only explicit human sample decisions count — tool results never include or exclude a sample on their own.</p>
      </header>

      <section className="export-form">
        <label className="correction-field">Audit
          <select value={auditId} onChange={(event) => setAuditId(event.target.value)}>
            <option value="">Select a completed audit</option>
            {exportableAudits.map((audit) => (
              <option key={audit.audit_id} value={audit.audit_id}>{audit.audit_id} · {audit.dataset_id} · rulebook {audit.rulebook_id} v{audit.rulebook_version}</option>
            ))}
          </select>
        </label>
        <p className="text-xs">Source format: {exportFormat ?? 'Select an audit'}</p>
        {datasets.error && <p role="alert">Could not load the source dataset format. Refresh to try again.</p>}
        <fieldset className="export-selection">
          <legend>Include</legend>
          {(Object.keys(SELECTION_TEXT) as ExportSelection[]).map((value) => (
            <label key={value} className={selection === value ? 'is-active' : ''}>
              <input type="radio" name="selection" value={value} checked={selection === value} onChange={() => setSelection(value)} />
              <span><b>{SELECTION_TEXT[value].title}</b><small>{SELECTION_TEXT[value].detail}</small></span>
            </label>
          ))}
        </fieldset>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={includeImages} onChange={(event) => setIncludeImages(event.target.checked)} /> Include image files in the ZIP</label>
        <p className="text-2xs text-text-faint">Samples still awaiting review or marked “needs correction” are always left out.</p>
        <div className="flex gap-2">
          <Button variant="primary" size="sm" disabled={!auditId || !exportFormat || start.isPending} isLoading={start.isPending} onClick={() => start.mutate()}>Start export</Button>
          <Button variant="secondary" size="sm" leftIcon={<RefreshCw size={13} />} onClick={() => void history.refetch()}>Refresh history</Button>
        </div>
        {message && <p role={message.tone === 'error' ? 'alert' : 'status'} className={`text-xs ${message.tone === 'error' ? 'text-status-fail' : 'text-status-pass'}`}>{message.text}</p>}
      </section>

      <section aria-label="Export history">
        <h2>Export history ({history.data?.total ?? 0})</h2>
        {history.isLoading ? <p className="text-xs text-text-muted">Loading exports…</p>
          : history.error ? <p role="alert" className="text-xs text-status-fail">{history.error.message}</p>
            : !history.data?.items.length ? <p className="text-xs text-text-muted">No exports yet.</p>
              : history.data.items.map((record) => <ExportHistoryRow key={record.export_id} record={record} />)}
      </section>
    </div>
  );
};
