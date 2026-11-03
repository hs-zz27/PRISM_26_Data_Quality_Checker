import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  Sparkles,
  ArrowRight,
  AlertCircle,
  Loader2,
} from 'lucide-react';
import { apiGetAllPages, apiPost } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { useJobPolling, type Job } from '../../hooks/useJobPolling';
import type { components } from '../../api/generated/openapi';
import type { operations } from '../../api/generated/openapi';
import { RulebookNameText } from '../../components/ui/RecordName';

type ExtractedJob = operations['extractRulebook']['responses'][202]['content']['application/json'];

export const RulebooksPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();

  const [guidelineText, setGuidelineText] = useState('');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [showExtractionStudio, setShowExtractionStudio] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);

  // Fetch all rulebooks
  const { data: rulebookPage, isLoading, error } = useQuery({
    queryKey: queryKeys.rulebooks(),
    queryFn: () => apiGetAllPages('/rulebooks', { query: { limit: 100, offset: 0 } }),
  });
  const rulebooks = rulebookPage?.items ?? [];

  // Track extraction job
  const jobState = useJobPolling(activeJobId, {
    onSuccess: (job: Job) => {
      if (job.result?.id) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
        navigate(`/rulebooks/${job.result.id}`);
      }
    },
  });

  // Extraction mutation
  const extractMutation = useMutation({
    mutationFn: (text: string) => {
      if (!reviewerId?.trim()) { openPrompt(); throw new Error('Reviewer ID required'); }
      return apiPost('/rulebooks/extractions', { body: { guideline_text: text } });
    },
    onSuccess: (job: ExtractedJob) => {
      setActiveJobId(job.job_id);
    },
  });

  const createMutation = useMutation({
    mutationFn: (guidelines: string) => {
      const body: components['schemas']['RulebookCreate'] = { guideline_text: guidelines, rules: [] };
      return apiPost('/rulebooks', { body });
    },
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
      navigate(`/rulebooks/${created.rulebook_id}?version=${created.version}`);
    },
  });

  const handleStartExtraction = () => {
    if (!reviewerId.trim()) {
      openPrompt();
      return;
    }
    if (!guidelineText.trim()) return;
    extractMutation.mutate(guidelineText);
  };

  // Quick count of potential sentences
  const sentenceCount = guidelineText
    .split(/\n+|\.\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 5).length;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-semibold text-text flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-accent" />
            Annotation Rulebooks
          </h1>
          <p className="text-xs text-text-muted mt-1">
            Rules extracted from natural language guidelines or authored by reviewers. Confirmed rulebooks are frozen for audits.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setShowCreateForm((open) => !open)}>
            New draft
          </Button>
          <Button
            variant={showExtractionStudio ? 'secondary' : 'primary'}
            leftIcon={<Sparkles className="w-4 h-4" />}
            onClick={() => setShowExtractionStudio(!showExtractionStudio)}
          >
            {showExtractionStudio ? 'Close Studio' : 'Extract from Guidelines'}
          </Button>
        </div>
      </div>

      {showCreateForm && (
        <section className="bg-surface border border-border rounded p-4 space-y-3">
          <h2 className="text-sm font-medium">Create an empty draft</h2>
          <p className="text-xs text-text-muted">Enter the exact guideline text. Rules can be added and reviewed in the editor.</p>
          <textarea value={guidelineText} onChange={(event) => setGuidelineText(event.target.value)} rows={5} className="w-full bg-base border border-border rounded p-3 text-xs font-mono" />
          {createMutation.isError && <p role="alert" className="text-xs text-status-fail">{createMutation.error.message}</p>}
          <Button disabled={createMutation.isPending} onClick={() => createMutation.mutate(guidelineText)}>Create draft</Button>
        </section>
      )}

      {/* Extraction Studio Accordion / Panel */}
      {showExtractionStudio && (
        <div className="bg-surface border border-accent/30 rounded-lg p-5 space-y-4 shadow-lg animate-in slide-in-from-top-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-text">
              <Sparkles className="w-4 h-4 text-accent" />
              <span>AI Rule Extraction Studio</span>
            </div>
            <div className="text-xs font-mono text-text-muted">
              {sentenceCount} candidate {sentenceCount === 1 ? 'sentence' : 'sentences'} detected
            </div>
          </div>

          <ol className="text-xs text-text-muted leading-relaxed list-decimal pl-5 space-y-0.5">
            <li>Open the guideline document in your usual authorised viewer — PRISM never opens the protected file.</li>
            <li>Copy the guideline text and paste it below as plain text.</li>
            <li>PRISM proposes rules, each citing the exact sentence it came from. You then confirm, reject, edit or add rules before any data is checked.</li>
          </ol>

          <textarea
            value={guidelineText}
            onChange={(e) => setGuidelineText(e.target.value)}
            rows={7}
            disabled={extractMutation.isPending || jobState.isActive}
            className="w-full bg-base border border-border rounded p-3 text-xs font-mono text-text placeholder:text-text-faint focus:outline-none focus:border-accent resize-y"
            placeholder="Type or paste annotation guidelines here..."
          />

          {/* Active Job Progress */}
          {jobState.isActive && (
            <div className="p-3 bg-surface-high border border-accent/40 rounded flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Loader2 className="w-4 h-4 text-accent animate-spin" />
                <div className="text-xs">
                  <div className="text-text font-medium">Extracting rules via LLM...</div>
                  <div className="text-text-muted text-2xs font-mono">
                    Phase: {jobState.progress?.phase ?? 'processing'} · Job ID: {activeJobId}
                  </div>
                </div>
              </div>
              <Button size="sm" variant="ghost" onClick={() => jobState.cancel()}>
                Cancel
              </Button>
            </div>
          )}

          {extractMutation.isError && (
            <div className="flex items-center gap-2 text-xs text-status-fail bg-status-fail/10 p-2.5 rounded border border-status-fail/20">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{(extractMutation.error as Error).message}</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button
              variant="primary"
              isLoading={extractMutation.isPending || jobState.isActive}
              onClick={handleStartExtraction}
            >
              Start Rule Extraction
            </Button>
          </div>
        </div>
      )}

      {/* Rulebooks Table / List */}
      <div>
        <div className="text-xs font-mono uppercase tracking-wider text-text-muted mb-3">
          Available Rulebooks ({rulebookPage?.total ?? 0})
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center p-12 text-text-muted text-xs">
            <Loader2 className="w-4 h-4 animate-spin mr-2 text-accent" />
            Loading rulebooks...
          </div>
        ) : error ? (
          <div className="p-4 rounded bg-status-fail/10 border border-status-fail/30 text-status-fail text-xs">
            Failed to load rulebooks: {(error as Error).message}
          </div>
        ) : rulebooks.length === 0 ? (
          <div className="p-12 text-center border border-dashed border-border rounded-lg bg-surface/50">
            <BookOpen className="w-8 h-8 text-text-muted mx-auto mb-2 opacity-60" />
            <div className="text-sm text-text font-medium">No rulebooks found</div>
            <p className="text-xs text-text-muted mt-1 max-w-sm mx-auto">
              Extract rules from your plain text annotation guidelines or create a draft rulebook to get started.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => setShowExtractionStudio(true)}
            >
              Extract Rules Now
            </Button>
          </div>
        ) : (
          <div className="border border-border rounded-lg overflow-hidden bg-surface">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-high border-b border-border text-2xs font-mono uppercase text-text-muted">
                <tr>
                  <th className="py-2.5 px-4 font-medium">Rulebook</th>
                  <th className="py-2.5 px-4 font-medium">Version</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                  <th className="py-2.5 px-4 font-medium">Rules</th>
                  <th className="py-2.5 px-4 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rulebooks.map((rb) => {
                  const state = rb.confirmation_state ?? 'draft';
                  return (
                    <tr
                      key={`${rb.rulebook_id}_v${rb.version}`}
                      className="hover:bg-surface-high/60 transition-colors cursor-pointer"
                      onClick={() => navigate(`/rulebooks/${rb.rulebook_id}?version=${rb.version}`)}
                    >
                      <td className="py-3 px-4 text-text max-w-[22rem]">
                        <RulebookNameText name={rb.name} />
                      </td>
                      <td className="py-3 px-4 font-mono text-text-muted">
                        v{rb.version}
                      </td>
                      <td className="py-3 px-4">
                        <Badge
                          variant={
                            state === 'confirmed'
                              ? 'pass'
                              : state === 'rejected'
                              ? 'fail'
                              : 'pending'
                          }
                          size="sm"
                        >
                          {state === 'draft' ? 'draft — needs review' : state}
                        </Badge>
                      </td>
                      <td className="py-3 px-4 text-text-muted">
                        {rb.rules?.length ?? 0} rules
                      </td>
                      <td className="py-3 px-4 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/rulebooks/${rb.rulebook_id}?version=${rb.version}`);
                          }}
                        >
                          Open Editor
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
    </div>
  );
};
