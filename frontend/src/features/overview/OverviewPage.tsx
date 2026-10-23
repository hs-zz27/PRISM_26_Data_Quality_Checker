import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, BookOpen, Check, Circle, Database, DownloadCloud, ListChecks, SearchCode } from 'lucide-react';
import { apiGetAllPages } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { formatTimestamp } from '../../lib/format';
import { jobStatusLabel } from '../../lib/labels';
import { workflowSteps, type WorkflowCounts } from './workflow';

const ICONS = [BookOpen, ListChecks, Database, SearchCode, DownloadCloud];

export const OverviewPage: React.FC = () => {
  const rulebooks = useQuery({ queryKey: queryKeys.rulebooks(), queryFn: () => apiGetAllPages('/rulebooks', { query: { limit: 100, offset: 0 } }) });
  const datasets = useQuery({ queryKey: queryKeys.datasets(), queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }) });
  const audits = useQuery({ queryKey: queryKeys.audits(), queryFn: () => apiGetAllPages('/audits', { query: { limit: 100, offset: 0 } }) });
  const exports = useQuery({ queryKey: queryKeys.exports(), queryFn: () => apiGetAllPages('/exports', { query: { limit: 100, offset: 0 } }) });

  const loading = rulebooks.isLoading || datasets.isLoading || audits.isLoading || exports.isLoading;
  const failed = rulebooks.error ?? datasets.error ?? audits.error ?? exports.error;
  const counts: WorkflowCounts = {
    draftRulebooks: rulebooks.data?.items.filter((book) => book.confirmation_state === 'draft').length ?? 0,
    confirmedRulebooks: rulebooks.data?.items.filter((book) => book.confirmation_state === 'confirmed').length ?? 0,
    readyDatasets: datasets.data?.items.filter((dataset) => dataset.status === 'ready').length ?? 0,
    audits: audits.data?.items.length ?? 0,
    completedAudits: audits.data?.items.filter((audit) => audit.status === 'completed').length ?? 0,
    exports: exports.data?.items.length ?? 0,
  };
  const steps = workflowSteps(counts);
  const recentAudits = [...(audits.data?.items ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5);

  return (
    <div className="workstation-page overview-page space-y-5">
      <header>
        <h1>PRISM data quality checker</h1>
        <p className="text-xs text-text-muted mt-1 max-w-3xl">
          The guideline is the rulebook: rules come only from the text you paste, every result cites its source sentence, and a person makes every final decision.
        </p>
      </header>

      {failed && <p role="alert" className="text-xs text-status-fail">Some information could not be loaded: {String(failed)}</p>}

      <ol className="workflow" aria-label="Workflow">
        {steps.map((step, index) => {
          const Icon = ICONS[index] ?? Circle;
          return (
            <li key={step.title} className={`workflow-step is-${step.state}`}>
              <span className="workflow-icon" aria-hidden="true">{step.state === 'done' ? <Check size={15} /> : <Icon size={15} />}</span>
              <div className="workflow-copy">
                <span className="workflow-number">Step {index + 1}{step.state === 'next' ? ' · do this next' : ''}</span>
                <b>{step.title}</b>
                <p>{step.description}</p>
                <small>{loading ? 'Checking…' : step.status}</small>
              </div>
              <Link to={step.to} className="workflow-action">{step.action} <ArrowRight size={13} /></Link>
            </li>
          );
        })}
      </ol>

      {recentAudits.length > 0 && (
        <section className="report-section">
          <h2>Recent audits</h2>
          <table className="report-table">
            <thead><tr><th>Audit</th><th>Dataset</th><th>Rulebook</th><th>Status</th><th>Started</th><th /></tr></thead>
            <tbody>
              {recentAudits.map((audit) => (
                <tr key={audit.audit_id}>
                  <td className="font-mono">{audit.audit_id}</td>
                  <td className="font-mono">{audit.dataset_id}</td>
                  <td className="font-mono">{audit.rulebook_id} v{audit.rulebook_version}</td>
                  <td>{jobStatusLabel(audit.status)} · {audit.processed_count}/{audit.sample_count ?? '?'}</td>
                  <td>{formatTimestamp(audit.created_at)}</td>
                  <td><Link to={`/audits/${audit.audit_id}/review`}>Review</Link> · <Link to={`/audits/${audit.audit_id}/report`}>Report</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
};
