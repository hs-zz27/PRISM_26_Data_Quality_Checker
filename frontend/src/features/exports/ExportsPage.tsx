import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiGetAllPages, apiPost, buildApiUrl } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Button } from '../../components/ui/Button';
import { useJobPolling } from '../../hooks/useJobPolling';
import type { components } from '../../api/generated/openapi';
import { getExportDetailPollInterval, getTrackedExportJob } from './exportPolling';

type ExportRecord = components['schemas']['Export'];

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
  const manifest = useQuery({
    queryKey: ['exports', record.export_id, 'manifest'],
    queryFn: () => apiGet('/exports/{export_id}/manifest', { path: { export_id: record.export_id } }),
    enabled: expanded && record.status === 'completed',
  });
  return (
    <article className="border-b border-border py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono">{record.export_id}</span>
        <span>{record.status}</span>
        <span className="text-text-muted">Audit {record.audit_id} · {record.selection}</span>
        {activeJobId && <span className="text-text-muted">Job {activeJobId}: {job.status ?? 'loading'}</span>}
        <Button size="sm" variant="secondary" onClick={() => setExpanded((open) => !open)}>{expanded ? 'Hide detail' : 'Detail / manifest'}</Button>
        {record.status === 'completed' ? <a href={buildApiUrl('/exports/{export_id}/download', { export_id: record.export_id })}>Download</a> : <span className="text-text-muted">Download unavailable until complete</span>}
      </div>
      {detail.error && <p role="alert">Export detail: {detail.error.message}</p>}
      {manifest.error && <p role="alert">Manifest: {manifest.error.message}</p>}
      {expanded && detail.data && <pre className="mt-2 overflow-auto">{JSON.stringify(detail.data, null, 2)}</pre>}
      {expanded && manifest.data && <pre className="mt-2 overflow-auto">{JSON.stringify(manifest.data, null, 2)}</pre>}
    </article>
  );
}

export const ExportsPage: React.FC = () => {
  const client = useQueryClient();
  const [auditId, setAuditId] = useState('');
  const [includeImages, setIncludeImages] = useState(true);
  const [message, setMessage] = useState('');
  const history = useQuery({
    queryKey: queryKeys.exports(),
    queryFn: () => apiGetAllPages('/exports', { query: { limit: 100, offset: 0 } }),
  });
  const start = useMutation({
    mutationFn: () => apiPost<'/exports'>('/exports', { body: { audit_id: auditId, format: 'penn_fudan', selection: 'accepted_only', include_images: includeImages } }),
    onSuccess: (result) => {
      setMessage(`Export ${result.export.export_id} queued (job ${result.job.job_id}).`);
      void client.invalidateQueries({ queryKey: queryKeys.exports() });
    },
    onError: (error) => setMessage(error.message),
  });
  return (
    <div className="workstation-page exports-page p-6 max-w-5xl mx-auto space-y-5">
      <h1 className="text-xl font-semibold">Dataset Export & Packaging</h1>
      <section className="flex flex-wrap items-center gap-3">
        <input value={auditId} onChange={(event) => setAuditId(event.target.value)} placeholder="Audit ID" />
        <label><input type="checkbox" checked={includeImages} onChange={(event) => setIncludeImages(event.target.checked)} /> Include images</label>
        <Button disabled={!auditId.trim() || start.isPending} onClick={() => start.mutate()}>Start export</Button>
        <Button variant="secondary" onClick={() => void history.refetch()}>Refresh history</Button>
      </section>
      {message && <p role="status">{message}</p>}
      <section aria-label="Export history">
        <h2>Export history ({history.data?.total ?? 0})</h2>
        {history.isLoading ? <p>Loading exports…</p> : history.error ? <p role="alert">{history.error.message}</p> : !history.data?.items.length ? <p>No exports yet.</p> : history.data.items.map((record) => <ExportHistoryRow key={record.export_id} record={record} />)}
      </section>
    </div>
  );
};
