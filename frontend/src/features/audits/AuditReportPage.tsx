import React, { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Download, FileJson, Loader2, Printer, Quote } from 'lucide-react';
import { apiGet, apiGetAllPages } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { checkLabel, jobStatusLabel, reviewStatusLabel, toolStatusLabel } from '../../lib/labels';
import { formatTimestamp } from '../../lib/format';
import { useDatasetName } from '../../hooks/useDatasetName';
import { aggregateByRule, buildReportRows, downloadText, mapWithConcurrency, reportToCsv, summarizeQueue } from './auditReport';

const pct = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 100)}%` : '—');

export const AuditReportPage: React.FC = () => {
  const { auditId } = useParams<{ auditId: string }>();
  const queryClient = useQueryClient();
  const [loaded, setLoaded] = useState(0);
  const datasetName = useDatasetName();

  const audit = useQuery({
    queryKey: queryKeys.audit(auditId!),
    queryFn: () => apiGet('/audits/{audit_id}', { path: { audit_id: auditId! } }),
    enabled: Boolean(auditId),
  });
  const queue = useQuery({
    queryKey: queryKeys.auditSamples(auditId!, { toolStatusFilter: 'all', reviewStatusFilter: 'all' }),
    queryFn: () => apiGetAllPages('/audits/{audit_id}/samples', { path: { audit_id: auditId! }, query: { limit: 100, offset: 0 } }),
    enabled: Boolean(auditId),
  });

  // Findings live on each sample record; load them with bounded concurrency and reuse the review cache.
  const details = useQuery({
    queryKey: ['audits', auditId, 'report-details', queue.data?.items.length],
    queryFn: async () => {
      setLoaded(0);
      return mapWithConcurrency(queue.data!.items, 4, (item) => queryClient.fetchQuery({
        queryKey: queryKeys.auditSample(auditId!, item.sample_id),
        queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', { path: { audit_id: auditId!, sample_id: item.sample_id } }),
      }), setLoaded);
    },
    enabled: Boolean(queue.data?.items.length),
    staleTime: 60_000,
  });

  const summary = useMemo(() => summarizeQueue(queue.data?.items ?? []), [queue.data]);
  const rules = useMemo(() => aggregateByRule(details.data ?? []), [details.data]);
  const rows = useMemo(() => buildReportRows(details.data ?? []), [details.data]);
  const total = summary.total;

  const exportCsv = () => downloadText(`${auditId}-report.csv`, reportToCsv(rows), 'text/csv;charset=utf-8');
  const exportJson = () => downloadText(`${auditId}-report.json`, JSON.stringify({
    audit: audit.data,
    generated_at: new Date().toISOString(),
    summary,
    rules,
    findings: rows,
  }, null, 2), 'application/json');

  return (
    <div className="workstation-page report-page space-y-5">
      <header className="report-header">
        <div>
          <Link to={`/audits/${auditId}/review`} className="report-back"><ArrowLeft size={13} /> Back to review</Link>
          <h1>Audit report</h1>
          <p className="report-meta">
            <span className="mono">{auditId}</span>
            {audit.data && <> · dataset <b>{datasetName(audit.data.dataset_id)}</b> · rulebook <span className="mono">{audit.data.rulebook_id} v{audit.data.rulebook_version}</span> · {jobStatusLabel(audit.data.status)} · started {formatTimestamp(audit.data.created_at)}</>}
          </p>
        </div>
        <div className="report-actions">
          <Button size="sm" variant="secondary" leftIcon={<Download size={13} />} disabled={!details.data} onClick={exportCsv}>CSV</Button>
          <Button size="sm" variant="secondary" leftIcon={<FileJson size={13} />} disabled={!details.data} onClick={exportJson}>JSON</Button>
          <Button size="sm" variant="secondary" leftIcon={<Printer size={13} />} onClick={() => window.print()}>Print</Button>
        </div>
      </header>

      {(audit.error || queue.error || details.error) && <p role="alert" className="text-status-fail text-xs">{String(audit.error ?? queue.error ?? details.error)}</p>}

      <section className="report-tiles" aria-label="Summary">
        <Tile label="Samples" value={total} detail={audit.data ? `${audit.data.processed_count} processed · ${audit.data.failed_count} failed` : undefined} />
        <Tile label="Reviewed" value={total - summary.review.pending_review} detail={`${pct(total - summary.review.pending_review, total)} of samples`} />
        <Tile label="Accepted" value={summary.review.accepted} detail={pct(summary.review.accepted, total)} tone="pass" />
        <Tile label="Rejected" value={summary.review.rejected} detail={pct(summary.review.rejected, total)} tone="fail" />
        <Tile label="Needs correction" value={summary.review.needs_correction} detail={pct(summary.review.needs_correction, total)} tone="unsure" />
      </section>

      <section className="report-section">
        <h2>What the tool found</h2>
        <div className="report-bars">
          {(['flagged', 'unsure', 'no_issue', 'unavailable', 'pending'] as const).map((key) => (
            <div key={key} className="report-bar-row">
              <span>{toolStatusLabel(key)}</span>
              <div className="report-bar"><span className={`is-${key}`} style={{ width: pct(summary.tool[key], total) === '—' ? '0%' : pct(summary.tool[key], total) }} /></div>
              <b className="mono">{summary.tool[key]}</b>
            </div>
          ))}
        </div>
        {summary.incomplete > 0 && <p className="text-2xs text-status-unsure">{summary.incomplete} sample(s) have incomplete processing — review them by eye.</p>}
      </section>

      <section className="report-section">
        <div className="flex items-center justify-between">
          <h2>Results by rule</h2>
          {details.isFetching && <span className="text-2xs text-text-muted"><Loader2 className="inline w-3 h-3 animate-spin" /> Loading findings {loaded} / {total}</span>}
        </div>
        {!details.data && !details.isFetching && total === 0 && <p className="text-xs text-text-muted">This audit has no samples yet.</p>}
        {rules.length > 0 && (
          <table className="report-table">
            <thead>
              <tr>
                <th>Rule</th><th>Check</th><th>Pass</th><th>Fail</th><th>Unsure</th><th>Could not check</th><th>Reviewer agreed</th><th>Overridden</th><th>Not reviewed</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => {
                const reviewed = rule.confirmed + rule.overridden + rule.unsure;
                return (
                  <tr key={rule.rule_id}>
                    <td>
                      <b className="mono">{rule.rule_id}</b> {rule.description}
                      {rule.source_sentence
                        ? <small className="report-citation"><Quote size={10} /> “{rule.source_sentence}”</small>
                        : <small className="report-citation">Reviewer-authored rule</small>}
                    </td>
                    <td>{rule.check_name ? checkLabel(rule.check_name) : 'Manual'}</td>
                    <td className="mono">{rule.outcomes.pass}</td>
                    <td className="mono text-status-fail">{rule.outcomes.fail}</td>
                    <td className="mono text-status-unsure">{rule.outcomes.unsure}</td>
                    <td className="mono">{rule.outcomes.unavailable}</td>
                    <td className="mono">{rule.confirmed}{reviewed ? <small> ({pct(rule.confirmed, reviewed)})</small> : null}</td>
                    <td className="mono">{rule.overridden}</td>
                    <td className="mono">{rule.unreviewed}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="text-2xs text-text-faint">“Reviewer agreed” is the share of reviewed findings where the human confirmed the tool’s result. It shows how often the tool matches reviewer judgement on this audit; it is not a measured accuracy against held-out ground truth.</p>
      </section>

      <section className="report-section">
        <h2>Samples</h2>
        <table className="report-table">
          <thead><tr><th>Sample</th><th>Tool result</th><th>Fail</th><th>Unsure</th><th>Decision</th><th /></tr></thead>
          <tbody>
            {(queue.data?.items ?? []).map((item) => {
              const detail = details.data?.find((sample) => sample.summary.sample_id === item.sample_id);
              return (
                <tr key={item.sample_id}>
                  <td className="mono">{item.sample_id} <small>r{item.sample_revision}</small></td>
                  <td>{toolStatusLabel(item.tool_status)}</td>
                  <td className="mono">{item.finding_counts.fail}</td>
                  <td className="mono">{item.finding_counts.unsure}</td>
                  <td>
                    <Badge size="sm" variant={item.review_status === 'accepted' ? 'accepted' : item.review_status === 'rejected' ? 'rejected' : item.review_status === 'needs_correction' ? 'needs-fix' : 'pending'}>{reviewStatusLabel(item.review_status)}</Badge>
                    {detail?.review?.comment && <small className="block text-text-muted">“{detail.review.comment}” — {detail.review.reviewer_id}</small>}
                  </td>
                  <td><Link to={`/audits/${auditId}/review?sample=${encodeURIComponent(item.sample_id)}`}>Open</Link></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
};

const Tile: React.FC<{ label: string; value: number; detail?: string; tone?: 'pass' | 'fail' | 'unsure' }> = ({ label, value, detail, tone }) => (
  <div className={`report-tile${tone ? ` is-${tone}` : ''}`}>
    <span>{label}</span>
    <b className="mono">{value}</b>
    {detail && <small>{detail}</small>}
  </div>
);
