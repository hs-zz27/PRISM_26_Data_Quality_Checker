import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2,
  XCircle,
  RotateCcw,
  Save,
  Lock,
  ArrowLeft,
  History,
  FileText,
  Layers,
} from 'lucide-react';
import { apiGet, apiPut, apiPost, isVersionConflict } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import type { components } from '../../api/generated/openapi';

type Rule = components['schemas']['Rule'];

export const RulebookEditorPage: React.FC = () => {
  const { rulebookId } = useParams<{ rulebookId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();

  const requestedVersion = searchParams.get('version')
    ? parseInt(searchParams.get('version')!, 10)
    : undefined;

  // Selected rule or sentence for bidirectional citation linking
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null);

  // Local draft state for editing
  const [localRules, setLocalRules] = useState<Rule[]>([]);
  const [localGuidelineText, setLocalGuidelineText] = useState<string>('');
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState<boolean>(false);
  const [conflictError, setConflictError] = useState<string | null>(null);
  const citationsPaneRef = useRef<HTMLDivElement>(null);
  const rulesPaneRef = useRef<HTMLDivElement>(null);

  // Fetch current rulebook
  const { data: rulebook } = useQuery({
    queryKey: queryKeys.rulebook(rulebookId!, requestedVersion),
    queryFn: () =>
      apiGet('/rulebooks/{rulebook_id}', { path: { rulebook_id: rulebookId! }, query: requestedVersion ? { version: requestedVersion } : {} }),
    enabled: Boolean(rulebookId),
  });
  const { data: capabilities } = useQuery({ queryKey: queryKeys.capabilities(), queryFn: () => apiGet('/capabilities') });

  // Fetch version history
  const { data: versionPage } = useQuery({
    queryKey: queryKeys.rulebookVersions(rulebookId!),
    queryFn: () => apiGet('/rulebooks/{rulebook_id}/versions', { path: { rulebook_id: rulebookId! }, query: { limit: 100, offset: 0 } }),
    enabled: Boolean(rulebookId),
  });

  const versions = versionPage?.items ?? [];
  const { data: reviewHistory, error: reviewHistoryError } = useQuery({
    queryKey: ['rulebook-reviews', rulebookId],
    queryFn: () => apiGet('/rulebooks/{rulebook_id}/reviews', { path: { rulebook_id: rulebookId! }, query: { limit: 100, offset: 0 } }),
    enabled: Boolean(rulebookId),
  });

  // Sync server data to local editor state
  useEffect(() => {
    if (rulebook) {
      setLocalRules(rulebook.rules ?? []);
      setLocalGuidelineText(rulebook.guideline_text ?? '');
      setHasUnsavedChanges(false);
      setConflictError(null);
    }
  }, [rulebook]);

  const isConfirmed = rulebook?.confirmation_state === 'confirmed';
  const isRejected = rulebook?.confirmation_state === 'rejected';
  const isReadOnly = isConfirmed || isRejected;

  useEffect(() => {
    if (!selectedRuleId) return;
    const citation = localRules.find((rule) => rule.rule_id === selectedRuleId)?.source_sentence?.trim();
    const ruleNode = Array.from(rulesPaneRef.current?.querySelectorAll<HTMLElement>('[data-rule-id]') ?? []).find((node) => node.dataset.ruleId === selectedRuleId);
    const citationNode = Array.from(citationsPaneRef.current?.querySelectorAll<HTMLElement>('[data-citation]') ?? []).find((node) => node.dataset.citation === citation);
    ruleNode?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    citationNode?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [selectedRuleId, localRules]);

  // Save draft mutation (PUT)
  const saveDraftMutation = useMutation({
    mutationFn: () => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }
      return apiPut('/rulebooks/{rulebook_id}', { path: { rulebook_id: rulebookId! }, body: { expected_version: rulebook?.version ?? 1, reviewer_id: activeReviewer, guideline_text: localGuidelineText, rules: localRules } });
    },
    onSuccess: () => {
      setHasUnsavedChanges(false);
      setConflictError(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebook(rulebookId!) });
    },
    onError: (err) => {
      if (isVersionConflict(err)) {
        setConflictError(
          'Version Conflict (409): Another reviewer updated this rulebook. Reload or revise to get the latest draft.',
        );
      }
    },
  });

  // Confirm rulebook mutation
  const confirmMutation = useMutation({
    mutationFn: () => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }
      return apiPost('/rulebooks/{rulebook_id}/confirm', { path: { rulebook_id: rulebookId! }, body: { expected_version: rulebook?.version ?? 1, reviewer_id: activeReviewer } });
    },
    onSuccess: () => {
      setHasUnsavedChanges(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebook(rulebookId!) });
    },
  });

  // Reject rulebook mutation
  const rejectMutation = useMutation({
    mutationFn: () => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }
      return apiPost('/rulebooks/{rulebook_id}/reject', { path: { rulebook_id: rulebookId! }, body: { expected_version: rulebook?.version ?? 1, reviewer_id: activeReviewer } });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebook(rulebookId!) });
    },
  });

  // Revise rulebook mutation (creates new draft version)
  const reviseMutation = useMutation({
    mutationFn: () => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }
      return apiPost('/rulebooks/{rulebook_id}/revisions', { path: { rulebook_id: rulebookId! }, body: { expected_version: rulebook?.version ?? 1, reviewer_id: activeReviewer } });
    },
    onSuccess: (newDraft) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
      setSearchParams({ version: String(newDraft.version) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebook(rulebookId!, newDraft.version) });
    },
  });

  // Split guidelines into sentences for exact citation matching
  const sentences = useMemo(() => {
    if (!localGuidelineText) return [];
    return localGuidelineText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
  }, [localGuidelineText]);

  // Find cited sentence for the currently selected rule
  const selectedRule = localRules.find((r) => r.rule_id === selectedRuleId);

  return (
    <div className="workstation-page rulebook-editor flex flex-col h-full bg-base overflow-hidden">
      {/* Top Action Bar */}
      <div className="h-14 border-b border-border bg-surface px-6 flex items-center justify-between shrink-0 select-none">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<ArrowLeft className="w-4 h-4" />}
            onClick={() => navigate('/rulebooks')}
          >
            Rulebooks
          </Button>

          <div className="h-4 w-px bg-border" />

          <div className="flex items-center gap-2">
            <span className="font-mono font-semibold text-text text-sm">
              {rulebookId}
            </span>
            <span className="font-mono text-xs text-text-muted">
              v{rulebook?.version ?? 1}
            </span>
            <Badge
              variant={
                isConfirmed ? 'pass' : isRejected ? 'fail' : 'pending'
              }
              size="sm"
            >
              {rulebook?.confirmation_state ?? 'draft'}
            </Badge>
          </div>

          {/* Version Selector */}
          {reviewHistoryError && <span role="alert" className="text-status-fail">Review history unavailable</span>}
          {reviewHistory?.items?.length ? <span title={reviewHistory.items.map((entry) => `${entry.reviewer_id}: ${entry.action}`).join('\n')} className="text-2xs font-mono text-text-muted">{reviewHistory.items.length} review records</span> : null}
          {versions.length > 1 && (
            <div className="flex items-center gap-1.5 ml-2">
              <History className="w-3.5 h-3.5 text-text-muted" />
              <select
                value={rulebook?.version ?? 1}
                onChange={(e) => setSearchParams({ version: e.target.value })}
                className="bg-surface-high border border-border rounded px-2 py-1 text-xs font-mono text-text focus:outline-none focus:border-accent"
              >
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version} ({v.confirmation_state})
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {isConfirmed ? (
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<RotateCcw className="w-4 h-4 text-accent" />}
              isLoading={reviseMutation.isPending}
              onClick={() => reviseMutation.mutate()}
            >
              Revise Rulebook (New Draft)
            </Button>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                leftIcon={<XCircle className="w-4 h-4 text-status-fail" />}
                isLoading={rejectMutation.isPending}
                onClick={() => rejectMutation.mutate()}
              >
                Reject Rulebook
              </Button>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Save className="w-4 h-4" />}
                isLoading={saveDraftMutation.isPending}
                disabled={!hasUnsavedChanges}
                onClick={() => saveDraftMutation.mutate()}
              >
                Save Draft
              </Button>
              <Button
                variant="primary"
                size="sm"
                leftIcon={<CheckCircle2 className="w-4 h-4" />}
                isLoading={confirmMutation.isPending}
                onClick={() => confirmMutation.mutate()}
              >
                Confirm Rulebook
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Conflict Alert Banner if 409 occurs */}
      {conflictError && (
        <div className="bg-status-fail/15 border-b border-status-fail/30 px-6 py-2.5 flex items-center justify-between text-xs text-status-fail">
          <span>{conflictError}</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void queryClient.invalidateQueries({
                queryKey: queryKeys.rulebook(rulebookId!),
              });
            }}
          >
            Reload Latest
          </Button>
        </div>
      )}

      {/* Frozen Alert Banner if confirmed */}
      {isConfirmed && (
        <div className="bg-surface-high border-b border-border px-6 py-2 flex items-center justify-between text-xs text-text-muted">
          <div className="flex items-center gap-2">
            <Lock className="w-3.5 h-3.5 text-accent" />
            <span>
              This rulebook version is <strong>confirmed and frozen</strong> for audits. Edits are disabled.
            </span>
          </div>
          <span className="font-mono text-2xs text-text-faint">
            Click &quot;Revise Rulebook&quot; to spawn a mutable draft version.
          </span>
        </div>
      )}

      {/* Split Workspace */}
      <div className="flex-1 flex min-h-0">
        {/* Left Pane: Guidelines & Exact Citation Highlighting */}
        <div className="w-1/2 border-r border-border flex flex-col min-h-0 bg-base-pure">
          <div className="p-3 border-b border-border bg-surface flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs font-medium text-text">
              <FileText className="w-4 h-4 text-accent" />
              <span>Natural Language Guidelines (Citations)</span>
            </div>
            <span className="text-2xs font-mono text-text-muted">
              Click sentence to link rule
            </span>
          </div>

          <div ref={citationsPaneRef} className="flex-1 p-6 overflow-y-auto font-mono text-xs leading-relaxed space-y-2">
            {sentences.map((sentence, idx) => {
              const matchingRules = localRules.filter(
                (r) => r.source_sentence?.trim() === sentence.trim(),
              );
              const isSelected =
                selectedRule?.source_sentence?.trim() === sentence.trim();
              const hasRule = matchingRules.length > 0;

              return (
                <div
                  key={idx}
                  data-citation={sentence.trim()}
                  onClick={() => {
                    if (matchingRules.length > 0 && matchingRules[0]) {
                      setSelectedRuleId(matchingRules[0].rule_id);
                    }
                  }}
                  className={`p-2 rounded cursor-pointer transition-all border ${
                    isSelected
                      ? 'bg-accent/15 border-accent text-accent font-medium shadow-sm'
                      : hasRule
                      ? 'bg-surface/60 border-border/60 text-text hover:border-accent/40'
                      : 'border-transparent text-text-faint hover:text-text-muted'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <span>{sentence}</span>
                    {hasRule && (
                      <span className="shrink-0 px-1.5 py-0.2 rounded bg-surface-high border border-border text-2xs font-mono text-accent">
                        {matchingRules.map((r) => r.rule_id).join(', ')}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Pane: Rule Cards & Validation */}
        <div className="w-1/2 flex flex-col min-h-0 bg-base">
          <div className="p-3 border-b border-border bg-surface flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs font-medium text-text">
              <Layers className="w-4 h-4 text-accent" />
              <span>Extracted & Authored Rules ({localRules.length})</span>
            </div>
            <span className="text-2xs font-mono text-text-muted">
              {localRules.filter((r) => r.confirmation_state === 'confirmed').length} confirmed
            </span>
          </div>

          <div ref={rulesPaneRef} className="flex-1 p-6 overflow-y-auto space-y-4">
            {localRules.map((rule) => {
              const isSelected = selectedRuleId === rule.rule_id;
              return (
                <div
                  key={rule.rule_id}
                  data-rule-id={rule.rule_id}
                  onClick={() => setSelectedRuleId(rule.rule_id)}
                  className={`p-4 rounded-lg border transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-surface border-accent shadow-md ring-1 ring-accent'
                      : 'bg-surface border-border hover:border-border-muted'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold text-xs text-text">
                        {rule.rule_id}
                      </span>
                      <Badge
                        variant={
                          rule.confirmation_state === 'confirmed'
                            ? 'pass'
                            : rule.confirmation_state === 'rejected'
                            ? 'fail'
                            : 'pending'
                        }
                        size="sm"
                      >
                        {rule.confirmation_state ?? 'draft'}
                      </Badge>
                      <span className="text-2xs font-mono text-text-faint uppercase">
                        {rule.origin}
                      </span>
                    </div>

                    {!isReadOnly && (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setLocalRules((prev) =>
                              prev.map((r) =>
                                r.rule_id === rule.rule_id
                                  ? { ...r, confirmation_state: 'confirmed' }
                                  : r,
                              ),
                            );
                            setHasUnsavedChanges(true);
                          }}
                          className="px-2 py-0.5 rounded text-2xs font-mono bg-status-pass/10 text-status-pass border border-status-pass/30 hover:bg-status-pass/20"
                        >
                          Confirm
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setLocalRules((prev) =>
                              prev.map((r) =>
                                r.rule_id === rule.rule_id
                                  ? { ...r, confirmation_state: 'rejected' }
                                  : r,
                              ),
                            );
                            setHasUnsavedChanges(true);
                          }}
                          className="px-2 py-0.5 rounded text-2xs font-mono bg-status-fail/10 text-status-fail border border-status-fail/30 hover:bg-status-fail/20"
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </div>

                  <p className="text-xs text-text mb-3 leading-normal">
                    {rule.description}
                  </p>

                  {/* Bound Check & Parameters */}
                  <div className="bg-surface-high/60 border border-border/50 rounded p-2.5 space-y-2 text-2xs font-mono">
                    <div className="flex items-center justify-between">
                      <span className="text-text-muted">Bound Check:</span>
                      <span className={rule.check_name && capabilities?.checks.some((check) => check.name === rule.check_name && check.implementation === 'implemented') ? 'text-status-pass' : 'text-status-unsure'} title="Check availability comes from the capabilities catalogue">
                        {rule.check_name ? `${rule.check_name} · ${capabilities?.checks.find((check) => check.name === rule.check_name)?.implementation ?? 'not in catalogue'}` : 'None (Unbound)'}
                      </span>
                    </div>

                    {rule.parameters && Object.keys(rule.parameters).length > 0 && (
                      <div className="pt-1.5 border-t border-border/40">
                        <span className="text-text-muted block mb-1">Parameters:</span>
                        <pre className="text-text font-mono overflow-x-auto text-3xs p-1.5 bg-base rounded">
                          {JSON.stringify(rule.parameters, null, 2)}
                        </pre>
                      </div>
                    )}

                    {rule.source_sentence && (
                      <div className="pt-1.5 border-t border-border/40 text-text-muted">
                        <span className="text-text-faint block">Cited Sentence:</span>
                        <span className="text-text italic">
                          &quot;{rule.source_sentence}&quot;
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
