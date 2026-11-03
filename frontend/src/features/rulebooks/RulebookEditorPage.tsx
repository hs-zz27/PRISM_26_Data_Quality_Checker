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
  Plus,
  RefreshCw,
  Pencil,
  Trash2,
  Loader2,
  AlertTriangle,
} from 'lucide-react';
import { ApiError, apiGet, apiPut, apiPost, isVersionConflict } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { useJobPolling } from '../../hooks/useJobPolling';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { checkLabel, describeRequirement } from '../../lib/labels';
import { formatTimestamp } from '../../lib/format';
import { RuleForm } from './RuleForm';
import {
  CHECK_PARAMETER_TEMPLATES,
  adoptProposal,
  rulesForDraftSave,
  confirmationBlockers,
  newProposals,
  rulesWithMissingCitations,
  segmentGuideline,
  suggestRuleId,
} from './rulebookEditing';
import type { components } from '../../api/generated/openapi';
import { RulebookNameText, TechnicalId } from '../../components/ui/RecordName';
import { TitleEditor } from '../names/TitleEditor';
import { rulebookLabel } from '../../lib/names';

type Rule = components['schemas']['Rule'];
type Rulebook = components['schemas']['RulebookRecord'];

// Used when the capability catalogue is unavailable (it is a planned endpoint).
const FALLBACK_CHECKS = Object.keys(CHECK_PARAMETER_TEMPLATES).map((name) => ({ name, implementation: 'implemented' }));

export const RulebookEditorPage: React.FC = () => {
  const { rulebookId } = useParams<{ rulebookId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();

  const requestedVersion = searchParams.get('version') ? parseInt(searchParams.get('version')!, 10) : undefined;

  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null);
  const [localRules, setLocalRules] = useState<Rule[]>([]);
  const [localGuidelineText, setLocalGuidelineText] = useState<string>('');
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [isAddingRule, setIsAddingRule] = useState(false);
  const [isEditingText, setIsEditingText] = useState(false);
  const [highlightedText, setHighlightedText] = useState('');
  const [extractionJobId, setExtractionJobId] = useState<string | null>(null);
  const [proposals, setProposals] = useState<Rule[] | null>(null);
  const citationsPaneRef = useRef<HTMLDivElement>(null);
  const rulesPaneRef = useRef<HTMLDivElement>(null);
  const loadedKeyRef = useRef<string | null>(null);

  const { data: rulebook, error: rulebookError, isLoading } = useQuery({
    queryKey: queryKeys.rulebook(rulebookId!, requestedVersion),
    queryFn: () => apiGet('/rulebooks/{rulebook_id}', { path: { rulebook_id: rulebookId! }, query: requestedVersion ? { version: requestedVersion } : {} }),
    enabled: Boolean(rulebookId),
  });
  const { data: capabilities } = useQuery({ queryKey: queryKeys.capabilities(), queryFn: () => apiGet('/capabilities'), retry: false });
  const checks = capabilities?.checks ?? FALLBACK_CHECKS;

  const { data: versionPage } = useQuery({
    queryKey: queryKeys.rulebookVersions(rulebookId!),
    queryFn: () => apiGet('/rulebooks/{rulebook_id}/versions', { path: { rulebook_id: rulebookId! }, query: { limit: 100, offset: 0 } }),
    enabled: Boolean(rulebookId),
  });
  const versions = versionPage?.items ?? [];
  const { data: reviewHistory } = useQuery({
    queryKey: queryKeys.rulebookReviews(rulebookId!),
    queryFn: () => apiGet('/rulebooks/{rulebook_id}/reviews', { path: { rulebook_id: rulebookId! }, query: { limit: 100, offset: 0 } }),
    enabled: Boolean(rulebookId),
  });

  // Load server state into the editor only when a different version is opened,
  // so background refetches never wipe unsaved edits.
  useEffect(() => {
    if (!rulebook) return;
    const key = `${rulebook.rulebook_id}:${rulebook.version}`;
    if (loadedKeyRef.current === key) return;
    loadedKeyRef.current = key;
    setLocalRules(rulebook.rules ?? []);
    setLocalGuidelineText(rulebook.guideline_text ?? '');
    setHasUnsavedChanges(false);
    setActionError(null);
    setEditingRuleId(null);
    setIsAddingRule(false);
    setIsEditingText(false);
  }, [rulebook]);

  const isConfirmed = rulebook?.confirmation_state === 'confirmed';
  const isRejected = rulebook?.confirmation_state === 'rejected';
  const isReadOnly = isConfirmed || isRejected;
  const latestVersion = versions.reduce((max, v) => Math.max(max, v.version), rulebook?.version ?? 0);
  const isLatest = !rulebook || rulebook.version >= latestVersion;
  const canEdit = !isReadOnly && isLatest;

  useEffect(() => {
    if (!selectedRuleId) return;
    rulesPaneRef.current?.querySelector<HTMLElement>(`[data-rule-id="${CSS.escape(selectedRuleId)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    citationsPaneRef.current?.querySelector<HTMLElement>(`[data-cites~="${CSS.escape(selectedRuleId)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [selectedRuleId]);

  const openVersion = (book: Rulebook) => {
    loadedKeyRef.current = null;
    setSearchParams({ version: String(book.version) });
    queryClient.setQueryData(queryKeys.rulebook(book.rulebook_id, book.version), book);
    void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
    void queryClient.invalidateQueries({ queryKey: queryKeys.rulebookVersions(book.rulebook_id) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.rulebookReviews(book.rulebook_id) });
  };

  const requireReviewer = (): string => {
    const activeReviewer = reviewerId?.trim();
    if (!activeReviewer) {
      openPrompt();
      throw new Error('Set your reviewer name first (top right).');
    }
    return activeReviewer;
  };

  const describeError = (err: unknown): string => {
    if (isVersionConflict(err)) return 'Someone else changed this rulebook since you opened it. Your edits are still here — open the latest version to compare, then reapply them.';
    if (err instanceof ApiError && err.details?.length) return `${err.message} ${err.details.map((d) => `${d.field}: ${d.message}`).join('; ')}`;
    return err instanceof Error ? err.message : String(err);
  };

  const saveDraft = (rules: Rule[]) => {
    const previous = rulebook!.rules ?? [];
    const previousIds = new Set(previous.map((rule) => rule.rule_id));
    const copied = rules.filter((rule) => !previousIds.has(rule.rule_id) && rule.origin === 'extracted');
    if (copied.length && !window.confirm('These proposals were extracted into a separate draft. Adding them here saves them as reviewer-added rules and preserves their citations. The original extracted draft remains unchanged. Continue?')) {
      throw new Error('Save cancelled. Your unsaved rules are still here.');
    }
    return apiPut('/rulebooks/{rulebook_id}', {
      path: { rulebook_id: rulebookId! },
      body: { expected_version: rulebook!.version, reviewer_id: requireReviewer(),
        guideline_text: localGuidelineText, rules: rulesForDraftSave(previous, rules) },
    });
  };

  const saveDraftMutation = useMutation({
    mutationFn: () => saveDraft(localRules),
    onSuccess: (saved) => { setNotice(`Draft saved as ${rulebookLabel(saved.name, saved.version)}.`); openVersion(saved); },
    onError: (err) => setActionError(describeError(err)),
  });

  // Confirmation needs every retained rule confirmed; rejected rules are removed first.
  const confirmMutation = useMutation({
    mutationFn: async () => {
      const activeReviewer = requireReviewer();
      const { undecided, rejected, retained } = confirmationBlockers(localRules);
      if (undecided.length) throw new Error(`Decide on every rule first — still pending: ${undecided.map((r) => r.rule_id).join(', ')}.`);
      if (!retained.length) throw new Error('A confirmed rulebook needs at least one rule.');
      if (rulesWithMissingCitations(retained, localGuidelineText).length) throw new Error('Some citations no longer appear in the guideline text. Fix them before confirming.');
      if (rejected.length && !window.confirm(`Rejected rules (${rejected.map((r) => r.rule_id).join(', ')}) will be left out of the confirmed version. They remain in this version's history. Continue?`)) {
        throw new Error('Confirmation cancelled.');
      }
      let expectedVersion = rulebook!.version;
      if (hasUnsavedChanges || rejected.length) {
        const saved = await saveDraft(retained);
        expectedVersion = saved.version;
      }
      return apiPost('/rulebooks/{rulebook_id}/confirm', { path: { rulebook_id: rulebookId! }, body: { expected_version: expectedVersion, reviewer_id: activeReviewer } });
    },
    onSuccess: (confirmed) => { setNotice(`Confirmed as ${rulebookLabel(confirmed.name, confirmed.version)}. It is now locked and can be used for audits.`); openVersion(confirmed); },
    onError: (err) => { if (!(err instanceof Error && err.message === 'Confirmation cancelled.')) setActionError(describeError(err)); },
  });

  const rejectMutation = useMutation({
    mutationFn: () => {
      if (!window.confirm('Reject this whole draft? It will be locked; you can start a new draft from it later.')) throw new Error('cancelled');
      return apiPost('/rulebooks/{rulebook_id}/reject', { path: { rulebook_id: rulebookId! }, body: { expected_version: rulebook!.version, reviewer_id: requireReviewer() } });
    },
    onSuccess: (rejected) => openVersion(rejected),
    onError: (err) => { if (!(err instanceof Error && err.message === 'cancelled')) setActionError(describeError(err)); },
  });

  const reviseMutation = useMutation({
    mutationFn: () => apiPost('/rulebooks/{rulebook_id}/revisions', { path: { rulebook_id: rulebookId! }, body: { expected_version: rulebook!.version, reviewer_id: requireReviewer() } }),
    onSuccess: (draft) => { setNotice(`Started draft ${rulebookLabel(draft.name, draft.version)}. All rules are back to pending for review.`); openVersion(draft); },
    onError: (err) => setActionError(describeError(err)),
  });

  // Re-check document: run extraction again on the current text and offer only new proposals.
  const extraction = useJobPolling(extractionJobId, {
    onSuccess: (job) => {
      setExtractionJobId(null);
      if (!job.result?.id) { setActionError('Extraction finished without a result.'); return; }
      void apiGet('/rulebooks/{rulebook_id}', { path: { rulebook_id: job.result.id }, query: job.result.version ? { version: job.result.version } : {} })
        .then((extracted) => {
          const fresh = newProposals(localRules, extracted.rules ?? []);
          setProposals(fresh);
          setNotice(fresh.length ? null : 'Re-check found no rules that are not already in this draft.');
        })
        .catch((err: unknown) => setActionError(describeError(err)));
    },
    onError: (job) => { setExtractionJobId(null); setActionError(job.error?.message ?? 'Re-check failed.'); },
    onCancelled: () => setExtractionJobId(null),
  });
  const recheckMutation = useMutation({
    mutationFn: () => {
      requireReviewer();
      return apiPost('/rulebooks/extractions', { body: { guideline_text: localGuidelineText } });
    },
    onSuccess: (job) => { setProposals(null); setExtractionJobId(job.job_id); },
    onError: (err) => setActionError(describeError(err)),
  });

  const updateRules = (next: (rules: Rule[]) => Rule[]) => {
    setLocalRules(next);
    setHasUnsavedChanges(true);
    setNotice(null);
  };
  const setRuleState = (ruleId: string, state: Rule['confirmation_state']) =>
    updateRules((rules) => rules.map((r) => (r.rule_id === ruleId ? { ...r, confirmation_state: state } : r)));

  const segments = useMemo(() => segmentGuideline(localGuidelineText, localRules), [localGuidelineText, localRules]);
  const missingCitations = useMemo(() => rulesWithMissingCitations(localRules, localGuidelineText), [localRules, localGuidelineText]);
  const blockers = useMemo(() => confirmationBlockers(localRules), [localRules]);
  const ruleIds = localRules.map((r) => r.rule_id);

  const captureSelection = () => {
    const text = window.getSelection()?.toString().trim() ?? '';
    if (text && localGuidelineText.includes(text)) setHighlightedText(text);
  };

  if (isLoading) return <div className="p-6 text-xs text-text-muted"><Loader2 className="inline w-4 h-4 animate-spin" /> Loading rulebook…</div>;
  if (rulebookError) return <div role="alert" className="p-6 text-xs text-status-fail">Could not load rulebook: {describeError(rulebookError)}</div>;

  return (
    <div className="workstation-page rulebook-editor flex flex-col h-full bg-base overflow-hidden">
      <div className="h-14 border-b border-border bg-surface px-6 flex items-center justify-between shrink-0 select-none gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />} onClick={() => navigate('/rulebooks')}>Rulebooks</Button>
          <div className="h-4 w-px bg-border" />
          <RulebookNameText name={rulebook?.name} version={rulebook?.version ?? 1} className="text-sm" />
          <Badge variant={isConfirmed ? 'pass' : isRejected ? 'fail' : 'pending'} size="sm">{rulebook?.confirmation_state ?? 'draft'}</Badge>
          {versions.length > 1 && (
            <label className="flex items-center gap-1.5 ml-2 text-xs">
              <History className="w-3.5 h-3.5 text-text-muted" />
              <span className="sr-only">Version</span>
              <select
                value={rulebook?.version ?? 1}
                onChange={(e) => {
                  if (hasUnsavedChanges && !window.confirm('Discard unsaved edits and open another version?')) return;
                  loadedKeyRef.current = null;
                  setSearchParams({ version: e.target.value });
                }}
                className="bg-surface-high border border-border rounded px-2 py-1 text-xs font-mono text-text"
              >
                {versions.map((v) => <option key={v.version} value={v.version}>v{v.version} ({v.confirmation_state})</option>)}
              </select>
            </label>
          )}
          {hasUnsavedChanges && <span className="text-2xs font-mono text-status-unsure">● unsaved changes</span>}
        </div>

        <div className="flex items-center gap-2">
          {isReadOnly ? (
            isLatest && (
              <Button variant="secondary" size="sm" leftIcon={<RotateCcw className="w-4 h-4 text-accent" />} isLoading={reviseMutation.isPending} onClick={() => reviseMutation.mutate()}>
                Start new draft from this
              </Button>
            )
          ) : canEdit && (
            <>
              <Button variant="ghost" size="sm" leftIcon={<XCircle className="w-4 h-4 text-status-fail" />} isLoading={rejectMutation.isPending} onClick={() => rejectMutation.mutate()}>Reject draft</Button>
              <Button variant="secondary" size="sm" leftIcon={<Save className="w-4 h-4" />} isLoading={saveDraftMutation.isPending} disabled={!hasUnsavedChanges} onClick={() => saveDraftMutation.mutate()}>Save draft</Button>
              <Button
                variant="primary"
                size="sm"
                leftIcon={<CheckCircle2 className="w-4 h-4" />}
                isLoading={confirmMutation.isPending}
                disabled={blockers.undecided.length > 0 || blockers.retained.length === 0}
                title={blockers.undecided.length ? `${blockers.undecided.length} rule(s) still need Confirm or Reject` : 'Lock this rulebook for audits'}
                onClick={() => confirmMutation.mutate()}
              >
                Confirm rulebook
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border bg-surface px-6 py-1.5">
        {rulebook && <TitleEditor kind="rulebook" id={rulebook.rulebook_id} name={rulebook.name} />}
        <span className="text-2xs text-text-muted">Every version of this rulebook shares the label {rulebookLabel(rulebook?.name)}.</span>
        <TechnicalId label="Rulebook ID" value={rulebookId} />
      </div>
      {actionError && (
        <div role="alert" className="editor-banner is-error">
          <AlertTriangle size={14} /> <span>{actionError}</span>
          <button onClick={() => setActionError(null)}>Dismiss</button>
        </div>
      )}
      {notice && <div role="status" className="editor-banner is-info"><span>{notice}</span><button onClick={() => setNotice(null)}>Dismiss</button></div>}
      {!isLatest && <div className="editor-banner is-info"><span>You are viewing an older version (v{rulebook?.version}). Latest is v{latestVersion}.</span><button onClick={() => { loadedKeyRef.current = null; setSearchParams({ version: String(latestVersion) }); }}>Open latest</button></div>}
      {isConfirmed && (
        <div className="editor-banner is-locked">
          <Lock className="w-3.5 h-3.5 text-accent" />
          <span>This version is <strong>confirmed and locked</strong>. Audits pin it exactly. To change rules, start a new draft.</span>
        </div>
      )}
      {canEdit && (
        <div className="editor-banner is-progress">
          <span><b>{localRules.length - blockers.undecided.length}</b> of <b>{localRules.length}</b> rules decided</span>
          {blockers.undecided.length > 0 && (
            <button onClick={() => updateRules((rules) => rules.map((r) => ((r.confirmation_state ?? 'draft') === 'draft' ? { ...r, confirmation_state: 'confirmed' } : r)))}>
              Confirm all {blockers.undecided.length} pending
            </button>
          )}
          {blockers.rejected.length > 0 && <span className="text-text-faint">{blockers.rejected.length} rejected rule(s) will be left out on confirm</span>}
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        {/* Left pane: guideline text with exact citations highlighted */}
        <div className="w-1/2 border-r border-border flex flex-col min-h-0 bg-base-pure">
          <div className="p-3 border-b border-border bg-surface flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-medium text-text">
              <FileText className="w-4 h-4 text-accent" />
              <span>Guideline text</span>
            </div>
            <div className="flex items-center gap-2">
              {canEdit && (
                <>
                  <Button size="sm" variant="ghost" leftIcon={<Pencil className="w-3.5 h-3.5" />} onClick={() => setIsEditingText((v) => !v)}>{isEditingText ? 'Done editing' : 'Edit text'}</Button>
                  <Button size="sm" variant="secondary" leftIcon={<RefreshCw className="w-3.5 h-3.5" />} isLoading={recheckMutation.isPending || extraction.isActive} disabled={!localGuidelineText.trim()} onClick={() => recheckMutation.mutate()} title="Run rule extraction again on this text and list any rules not already here">
                    Re-check document
                  </Button>
                </>
              )}
            </div>
          </div>

          {extraction.isActive && (
            <div className="editor-inline-status">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-accent" /> Re-checking the guideline… ({extraction.progress?.phase ?? 'queued'})
              <button onClick={() => extraction.cancel()}>Cancel</button>
            </div>
          )}

          {missingCitations.length > 0 && (
            <div className="editor-inline-status is-warning">
              <AlertTriangle size={13} /> Citations not found in the text for: {missingCitations.map((r) => r.rule_id).join(', ')}. Edit those rules or restore the text before saving.
            </div>
          )}

          {isEditingText ? (
            <textarea
              aria-label="Guideline text"
              className="flex-1 m-3 p-3 bg-base border border-border rounded font-mono text-xs leading-relaxed resize-none"
              value={localGuidelineText}
              onChange={(e) => { setLocalGuidelineText(e.target.value); setHasUnsavedChanges(true); }}
            />
          ) : (
            <div ref={citationsPaneRef} className="guideline-text flex-1 p-6 overflow-y-auto" onMouseUp={captureSelection}>
              {segments.length === 0 && <p className="text-text-faint">No guideline text.</p>}
              {segments.map((segment, index) => segment.ruleIds.length ? (
                <mark
                  key={index}
                  data-cites={segment.ruleIds.join(' ')}
                  className={segment.ruleIds.includes(selectedRuleId ?? '') ? 'is-selected' : ''}
                  title={`Cited by ${segment.ruleIds.join(', ')}`}
                  onClick={() => setSelectedRuleId(segment.ruleIds[0] ?? null)}
                >
                  {segment.text}
                </mark>
              ) : <span key={index}>{segment.text}</span>)}
            </div>
          )}
          {canEdit && !isEditingText && <p className="guideline-hint">Tip: highlight any sentence above, then use it as the citation when you add a missed rule.</p>}
        </div>

        {/* Right pane: rule checklist */}
        <div className="w-1/2 flex flex-col min-h-0 bg-base">
          <div className="p-3 border-b border-border bg-surface flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs font-medium text-text">
              <Layers className="w-4 h-4 text-accent" />
              <span>Rules ({localRules.length})</span>
            </div>
            {canEdit && (
              <Button size="sm" variant="primary" leftIcon={<Plus className="w-3.5 h-3.5" />} onClick={() => { setIsAddingRule(true); setEditingRuleId(null); }}>
                Add missed rule
              </Button>
            )}
          </div>

          <div ref={rulesPaneRef} className="flex-1 p-4 overflow-y-auto space-y-3">
            {proposals && proposals.length > 0 && (
              <section className="proposal-panel">
                <header>
                  <b>Re-check found {proposals.length} new proposal{proposals.length === 1 ? '' : 's'}</b>
                  <span className="flex gap-2">
                    <button onClick={() => { updateRules((rules) => [...rules, ...proposals.map(adoptProposal)]); setProposals(null); }}>Adopt all</button>
                    <button onClick={() => setProposals(null)}>Dismiss</button>
                  </span>
                </header>
                {proposals.map((proposal) => (
                  <div key={proposal.rule_id} className="proposal-row">
                    <div>
                      <b className="font-mono">{proposal.rule_id}</b> {proposal.description}
                      {proposal.source_sentence && <small>“{proposal.source_sentence}”</small>}
                    </div>
                    <button onClick={() => { updateRules((rules) => [...rules, adoptProposal(proposal)]); setProposals((list) => list?.filter((p) => p.rule_id !== proposal.rule_id) ?? null); }}>Adopt</button>
                  </div>
                ))}
                <p className="text-2xs text-text-faint">Adopting a proposal here creates a reviewer-added rule with its citation preserved. The original extracted draft remains in the rulebook list.</p>
              </section>
            )}

            {isAddingRule && (
              <RuleForm
                suggestedId={suggestRuleId(localRules)}
                guidelineText={localGuidelineText}
                otherRuleIds={ruleIds}
                selectedText={highlightedText}
                checks={checks}
                onCancel={() => setIsAddingRule(false)}
                onSave={(rule) => { updateRules((rules) => [...rules, rule]); setIsAddingRule(false); setSelectedRuleId(rule.rule_id); }}
              />
            )}

            {localRules.length === 0 && !isAddingRule && (
              <p className="text-xs text-text-muted">No rules yet. {canEdit ? 'Add one by hand, or use “Re-check document” to extract rules from the text.' : ''}</p>
            )}

            {localRules.map((rule) => {
              const isSelected = selectedRuleId === rule.rule_id;
              const state = rule.confirmation_state ?? 'draft';
              const requirement = describeRequirement(rule.check_name, rule.parameters as Record<string, unknown>);
              const checkKnown = rule.check_name ? checks.find((check) => check.name === rule.check_name) : undefined;
              if (editingRuleId === rule.rule_id) {
                return (
                  <RuleForm
                    key={rule.rule_id}
                    initial={rule}
                    suggestedId={rule.rule_id}
                    guidelineText={localGuidelineText}
                    otherRuleIds={ruleIds.filter((id) => id !== rule.rule_id)}
                    selectedText={highlightedText}
                    checks={checks}
                    onCancel={() => setEditingRuleId(null)}
                    onSave={(updated) => { updateRules((rules) => rules.map((r) => (r.rule_id === rule.rule_id ? updated : r))); setEditingRuleId(null); }}
                  />
                );
              }
              return (
                <div
                  key={rule.rule_id}
                  data-rule-id={rule.rule_id}
                  onClick={() => setSelectedRuleId(rule.rule_id)}
                  className={`rule-card is-${state}${isSelected ? ' is-selected' : ''}`}
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-semibold text-xs text-text">{rule.rule_id}</span>
                      <Badge variant={state === 'confirmed' ? 'pass' : state === 'rejected' ? 'fail' : 'pending'} size="sm">{state === 'draft' ? 'needs decision' : state}</Badge>
                      <span className="text-2xs font-mono text-text-faint">{rule.origin === 'reviewer' ? 'added by reviewer' : 'extracted'}</span>
                    </div>
                    {canEdit && (
                      <div className="flex items-center gap-1">
                        <button type="button" className="rule-action is-confirm" aria-pressed={state === 'confirmed'} onClick={(e) => { e.stopPropagation(); setRuleState(rule.rule_id, state === 'confirmed' ? 'draft' : 'confirmed'); }}>
                          {state === 'confirmed' ? 'Confirmed ✓' : 'Confirm'}
                        </button>
                        <button type="button" className="rule-action is-reject" aria-pressed={state === 'rejected'} onClick={(e) => { e.stopPropagation(); setRuleState(rule.rule_id, state === 'rejected' ? 'draft' : 'rejected'); }}>
                          {state === 'rejected' ? 'Rejected ✕' : 'Reject'}
                        </button>
                        <button type="button" className="rule-action" title="Edit rule" aria-label={`Edit ${rule.rule_id}`} onClick={(e) => { e.stopPropagation(); setEditingRuleId(rule.rule_id); setIsAddingRule(false); }}><Pencil size={12} /></button>
                        {rule.origin === 'reviewer' && (
                          <button type="button" className="rule-action is-reject" title="Remove reviewer-added rule" aria-label={`Remove ${rule.rule_id}`} onClick={(e) => { e.stopPropagation(); updateRules((rules) => rules.filter((r) => r.rule_id !== rule.rule_id)); }}><Trash2 size={12} /></button>
                        )}
                      </div>
                    )}
                  </div>

                  <p className="text-xs text-text mb-2 leading-normal">{rule.description}</p>

                  <dl className="rule-facts">
                    <div>
                      <dt>Check</dt>
                      <dd className={rule.check_name && checkKnown?.implementation !== 'implemented' ? 'text-status-unsure' : ''}>
                        {rule.check_name
                          ? `${checkLabel(rule.check_name)}${checkKnown ? (checkKnown.implementation === 'implemented' ? '' : ' (planned — will report “could not check”)') : ' (not in catalogue — will report “could not check”)'}`
                          : 'None — reviewers check this by eye'}
                      </dd>
                    </div>
                    {requirement && <div><dt>Requires</dt><dd>{requirement}</dd></div>}
                    <div>
                      <dt>Source</dt>
                      <dd>{rule.source_sentence ? <em>“{rule.source_sentence}”</em> : <span className="text-text-faint">No citation (reviewer-authored)</span>}</dd>
                    </div>
                  </dl>
                </div>
              );
            })}

            {reviewHistory?.items?.length ? (
              <details className="rule-history">
                <summary>Review history ({reviewHistory.items.length})</summary>
                <ol>
                  {reviewHistory.items.map((entry) => (
                    <li key={`${entry.version}-${entry.action}`}>v{entry.version} · {entry.action} · {entry.reviewer_id}{entry.created_at ? ` · ${formatTimestamp(entry.created_at)}` : ''}</li>
                  ))}
                </ol>
              </details>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};
