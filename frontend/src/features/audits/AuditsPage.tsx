import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  SearchCode,
  Play,
  ArrowRight,
  AlertTriangle,
  Loader2,
  X,
} from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import { apiGetAllPages, apiPost } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { useJobPolling } from '../../hooks/useJobPolling';
import { formatCount, formatTimestamp } from '../../lib/format';
import { jobStatusLabel } from '../../lib/labels';
import { useDatasetName } from '../../hooks/useDatasetName';
import { unwrapAuditStarted } from '../../lib/audit';

export const AuditsPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const datasetName = useDatasetName();

  const [isLaunchModalOpen, setIsLaunchModalOpen] = useState(false);
  const [selectedDatasetId, setSelectedDatasetId] = useState('');
  const [selectedRulebookId, setSelectedRulebookId] = useState('');
  const [selectedRulebookVersion, setSelectedRulebookVersion] = useState<number>(1);
  const [selectedPolicyId, setSelectedPolicyId] = useState('');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeAuditId, setActiveAuditId] = useState<string | null>(null);

  // Fetch audits
  const { data: audits, isLoading, error } = useQuery({
    queryKey: queryKeys.audits(),
    queryFn: () => apiGetAllPages('/audits', { query: { limit: 100, offset: 0 } }),
  });

  // Fetch ready datasets for launch
  const { data: datasets } = useQuery({
    queryKey: queryKeys.datasets(),
    queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }),
  });

  // Fetch confirmed rulebooks for launch
  const { data: rulebooks } = useQuery({
    queryKey: queryKeys.rulebooks(),
    queryFn: () => apiGetAllPages('/rulebooks', { query: { limit: 100, offset: 0 } }),
  });

  // Activated feedback policies can be applied to new audits (the "Improve" step).
  const { data: policies } = useQuery({
    queryKey: queryKeys.feedbackPolicies(),
    queryFn: () => apiGetAllPages('/feedback/policies', { query: { limit: 100, offset: 0 } }),
    enabled: isLaunchModalOpen,
  });
  const activePolicies = policies?.items.filter((policy) => policy.status === 'active') ?? [];

  const readyDatasets = datasets?.items.filter((d) => d.status === 'ready') ?? [];
  const confirmedRulebooks = rulebooks?.items.filter((r) => r.confirmation_state === 'confirmed') ?? [];

  // Poll audit launch job
  const jobState = useJobPolling(activeJobId, {
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
      if (activeAuditId) navigate(`/audits/${activeAuditId}/review`);
    },
  });

  // Launch audit mutation
  const launchMutation = useMutation({
    mutationFn: () => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }
      return apiPost('/audits', {
        body: {
          dataset_id: selectedDatasetId,
          rulebook_id: selectedRulebookId,
          rulebook_version: selectedRulebookVersion,
          sample_ids: null,
          feedback_policy_id: selectedPolicyId || null,
        },
      });
    },
    onSuccess: (started) => {
      setIsLaunchModalOpen(false);
      const { audit, job } = unwrapAuditStarted(started);
      setActiveAuditId(audit.audit_id);
      setActiveJobId(job.job_id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
    },
  });

  const handleLaunch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!reviewerId.trim()) {
      openPrompt();
      return;
    }
    launchMutation.mutate();
  };

  return (
    <div className="workstation-page audits-page p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-semibold text-text flex items-center gap-2">
            <SearchCode className="w-5 h-5 text-accent" />
            Quality Audits & Workstation
          </h1>
          <p className="text-xs text-text-muted mt-1">
            Run batch detector and rule evaluation against dataset revisions. Human reviewers inspect machine findings and record final data decisions.
          </p>
        </div>
        <Button
          variant="primary"
          leftIcon={<Play className="w-4 h-4 fill-current" />}
          onClick={() => {
            const firstDataset = readyDatasets[0];
            if (firstDataset) setSelectedDatasetId(firstDataset.dataset_id);
            const firstRulebook = confirmedRulebooks[0];
            if (firstRulebook) {
              setSelectedRulebookId(firstRulebook.rulebook_id);
              setSelectedRulebookVersion(firstRulebook.version);
            }
            setIsLaunchModalOpen(true);
          }}
        >
          Launch New Audit
        </Button>
      </div>

      {/* Active Audit Job Banner */}
      {jobState.isActive && (
        <div className="p-4 bg-surface border border-accent/40 rounded-lg flex items-center justify-between shadow-md">
          <div className="flex items-center gap-3">
            <Loader2 className="w-5 h-5 text-accent animate-spin" />
            <div>
              <div className="text-sm font-semibold text-text">Running batch quality audit...</div>
              <div className="text-xs text-text-muted font-mono mt-0.5">
                Phase: {jobState.progress?.phase ?? 'evaluating'} · Processed:{' '}
                {jobState.progress?.processed ?? 0}
                {jobState.progress?.total ? ` / ${jobState.progress.total}` : ''}
              </div>
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={() => jobState.cancel()}>
            Cancel Audit
          </Button>
        </div>
      )}

      {/* Audits Table */}
      <div>
        <div className="text-xs font-mono uppercase tracking-wider text-text-muted mb-3">
          Executed Audits ({audits?.total ?? 0})
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center p-12 text-text-muted text-xs">
            <Loader2 className="w-4 h-4 animate-spin mr-2 text-accent" />
            Loading audits...
          </div>
        ) : error ? (
          <div className="p-4 rounded bg-status-fail/10 border border-status-fail/30 text-status-fail text-xs">
            Failed to load audits: {(error as Error).message}
          </div>
        ) : audits?.items.length === 0 ? (
          <div className="p-12 text-center border border-dashed border-border rounded-lg bg-surface/50">
            <SearchCode className="w-8 h-8 text-text-muted mx-auto mb-2 opacity-60" />
            <div className="text-sm text-text font-medium">No audits found</div>
            <p className="text-xs text-text-muted mt-1 max-w-sm mx-auto">
              Launch an audit against a ready dataset using a confirmed rulebook to begin reviewing samples on the canvas.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => setIsLaunchModalOpen(true)}
            >
              Launch Audit Now
            </Button>
          </div>
        ) : (
          <div className="border border-border rounded-lg overflow-hidden bg-surface">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-high border-b border-border text-2xs font-mono uppercase text-text-muted">
                <tr>
                  <th className="py-2.5 px-4 font-medium">Audit ID</th>
                  <th className="py-2.5 px-4 font-medium">Dataset</th>
                  <th className="py-2.5 px-4 font-medium">Rulebook</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                  <th className="py-2.5 px-4 font-medium">Samples</th>
                  <th className="py-2.5 px-4 font-medium">Processed</th>
                  <th className="py-2.5 px-4 font-medium">Created</th>
                  <th className="py-2.5 px-4 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {audits?.items.map((audit) => {
                  return (
                    <tr
                      key={audit.audit_id}
                      className="hover:bg-surface-high/60 transition-colors cursor-pointer"
                      onClick={() => navigate(`/audits/${audit.audit_id}/review`)}
                    >
                      <td className="py-3 px-4 font-mono font-medium text-text">
                        {audit.audit_id}
                      </td>
                      <td className="py-3 px-4 font-mono text-text-muted">
                        {datasetName(audit.dataset_id)}
                      </td>
                      <td className="py-3 px-4 font-mono text-text-muted">
                        {audit.rulebook_id} (v{audit.rulebook_version})
                      </td>
                      <td className="py-3 px-4">
                        <Badge
                          variant={
                            audit.status === 'completed'
                              ? 'pass'
                              : audit.status === 'failed' || audit.status === 'cancelled'
                              ? 'fail'
                              : 'pending'
                          }
                          size="sm"
                        >
                          {jobStatusLabel(audit.status)}
                        </Badge>
                      </td>
                      <td className="py-3 px-4 font-mono text-text">
                        {formatCount(audit.sample_count ?? null)}
                      </td>
                      <td className="py-3 px-4 font-mono text-text-muted">
                        {formatCount(audit.processed_count)}
                        {audit.failed_count > 0 && <span className="text-status-fail"> · {audit.failed_count} failed</span>}
                        {audit.incomplete_count > 0 && <span className="text-status-unsure"> · {audit.incomplete_count} incomplete</span>}
                      </td>
                      <td className="py-3 px-4 text-text-faint font-mono text-2xs">
                        {audit.created_at ? formatTimestamp(audit.created_at) : '—'}
                      </td>
                      <td className="py-3 px-4 text-right whitespace-nowrap">
                        <Link
                          to={`/audits/${audit.audit_id}/report`}
                          onClick={(e) => e.stopPropagation()}
                          className="mr-3 text-2xs text-text-muted hover:text-accent"
                        >
                          Report
                        </Link>
                        <Button
                          variant="primary"
                          size="sm"
                          rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/audits/${audit.audit_id}/review`);
                          }}
                        >
                          Review
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Launch Audit Modal */}
      <Dialog.Root open={isLaunchModalOpen} onOpenChange={setIsLaunchModalOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-base-pure/80 backdrop-blur-sm z-50 animate-in fade-in" />
          <Dialog.Content className="launch-drawer fixed right-0 top-10 h-[calc(100vh-40px)] w-full max-w-lg bg-surface border-l border-border p-5 z-50">
            <div className="flex items-center justify-between pb-3 border-b border-border mb-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-text">
                <Play className="w-4 h-4 text-accent fill-current" />
                <span>Launch Quality Audit</span>
              </div>
              <button
                onClick={() => setIsLaunchModalOpen(false)}
                className="text-text-muted hover:text-text p-1 rounded"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleLaunch} className="space-y-4">
              <div>
                <label className="block text-xs font-mono text-text-muted uppercase mb-1">
                  Target Dataset
                </label>
                {readyDatasets.length === 0 ? (
                  <div className="p-3 bg-surface-high border border-border rounded text-xs text-text-muted">
                    No ready datasets available. Import a dataset archive first.
                  </div>
                ) : (
                  <select
                    value={selectedDatasetId}
                    onChange={(e) => setSelectedDatasetId(e.target.value)}
                    required
                    className="w-full bg-surface-high border border-border rounded px-3 py-2 text-xs font-mono text-text focus:outline-none focus:border-accent"
                  >
                    {readyDatasets.map((d) => (
                      <option key={d.dataset_id} value={d.dataset_id}>
                        {d.name} ({d.dataset_id}) — {d.sample_count} samples
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div>
                <label className="block text-xs font-mono text-text-muted uppercase mb-1">
                  Confirmed Rulebook
                </label>
                {confirmedRulebooks.length === 0 ? (
                  <div className="p-3 bg-surface-high border border-status-unsure/30 rounded text-xs text-status-unsure">
                    No confirmed rulebooks found. An audit pins an exact confirmed rulebook version. Confirm a rulebook first.
                  </div>
                ) : (
                  <select
                    value={`${selectedRulebookId}:${selectedRulebookVersion}`}
                    onChange={(e) => {
                      const parts = e.target.value.split(':');
                      if (parts[0] && parts[1]) {
                        setSelectedRulebookId(parts[0]);
                        setSelectedRulebookVersion(parseInt(parts[1], 10));
                      }
                    }}
                    required
                    className="w-full bg-surface-high border border-border rounded px-3 py-2 text-xs font-mono text-text focus:outline-none focus:border-accent"
                  >
                    {confirmedRulebooks.map((r) => (
                      <option
                        key={`${r.rulebook_id}:${r.version}`}
                        value={`${r.rulebook_id}:${r.version}`}
                      >
                        {r.rulebook_id} (v{r.version}) — {r.rules?.length ?? 0} confirmed rules
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div>
                <label className="block text-xs font-mono text-text-muted uppercase mb-1">
                  Feedback policy (optional)
                </label>
                <select
                  value={selectedPolicyId}
                  onChange={(e) => setSelectedPolicyId(e.target.value)}
                  className="w-full bg-surface-high border border-border rounded px-3 py-2 text-xs font-mono text-text focus:outline-none focus:border-accent"
                >
                  <option value="">None — baseline checks only</option>
                  {activePolicies.map((policy) => (
                    <option key={policy.policy_id} value={policy.policy_id}>
                      {policy.name} ({policy.policy_id})
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-2xs text-text-faint">
                  Only policies activated after a held-out evaluation are listed. They add reviewer examples to AI-assisted checks.
                </p>
              </div>

              <div className="p-3 rounded bg-base border border-border text-2xs text-text-muted space-y-1">
                <div className="font-semibold text-text">Audit Execution Policy:</div>
                <div>• Audits pin exact rulebook version {selectedRulebookVersion}.</div>
                <div>• Machine findings are generated as proposals, never automated verdicts.</div>
                <div>• Reviewer attribution ({reviewerId}) will record all audit review actions.</div>
              </div>

              {launchMutation.isError && (
                <div className="p-2.5 rounded bg-status-fail/10 border border-status-fail/30 text-xs text-status-fail flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>{(launchMutation.error as Error).message}</span>
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t border-border">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setIsLaunchModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={launchMutation.isPending}
                  disabled={readyDatasets.length === 0 || confirmedRulebooks.length === 0}
                >
                  Launch Audit
                </Button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
};
