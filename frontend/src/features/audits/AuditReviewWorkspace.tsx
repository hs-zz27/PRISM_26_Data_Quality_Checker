import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Wrench,
  Filter,
  ChevronLeft,
  ChevronRight,
  Keyboard,
  Loader2,
  Eye,
  EyeOff,
} from 'lucide-react';
import { ApiError, apiGet, apiGetAllPages, apiPut } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { useCanvasStore } from '../../store/canvasStore';
import { Badge, type BadgeVariant } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { ReviewCanvas, type CanvasObject } from './ReviewCanvas';
import { KeyboardHelpModal } from '../../components/ui/KeyboardHelpModal';
import { isTypingTarget } from '../../lib/keyboard';
import { persistPaneWidth, readPaneWidth, resolveReviewKey } from './reviewWorkspace';
import type { components } from '../../api/generated/openapi';
import type { operations } from '../../api/generated/openapi';

type SampleAudit = components['schemas']['SampleAudit'];
type Finding = components['schemas']['Finding'];
type SampleReviewDecision = components['schemas']['SampleReviewDecision'];
type FindingResolution = components['schemas']['FindingResolution'];
type AuditQueueQuery = NonNullable<operations['listAuditSamples']['parameters']['query']>;
type AuditToolStatus = NonNullable<AuditQueueQuery['tool_status']>;
type AuditReviewStatus = NonNullable<AuditQueueQuery['review_status']>;

export const AuditReviewWorkspace: React.FC = () => {
  const { auditId } = useParams<{ auditId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const { toggleMask, toggleBoxes, resetView, zoom, setZoom } = useCanvasStore();

  const [activeSampleId, setActiveSampleId] = useState<string | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState<number | null>(null);
  const [hiddenObjectIds, setHiddenObjectIds] = useState<number[]>([]);
  const [reviewComment, setReviewComment] = useState<string>('');
  const [isKeyboardHelpOpen, setIsKeyboardHelpOpen] = useState<boolean>(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [historyFindingId, setHistoryFindingId] = useState<string | null>(null);
  const [leftWidth, setLeftWidth] = useState(() => readPaneWidth('left', 264));
  const [rightWidth, setRightWidth] = useState(() => readPaneWidth('right', 360));

  // Filters for the queue
  const [toolStatusFilter, setToolStatusFilter] = useState<'all' | AuditToolStatus>('all');
  const [reviewStatusFilter, setReviewStatusFilter] = useState<'all' | AuditReviewStatus>('all');

  // Fetch audit run details
  const { data: audit, error: auditError } = useQuery({
    queryKey: queryKeys.audit(auditId!),
    queryFn: () => apiGet('/audits/{audit_id}', { path: { audit_id: auditId! } }),
    enabled: Boolean(auditId),
  });

  // Fetch samples list for this audit
  const { data: samplePage, isLoading: isQueueLoading, error: queueError } = useQuery({
    queryKey: queryKeys.auditSamples(auditId!, { toolStatusFilter, reviewStatusFilter }),
    queryFn: () => apiGetAllPages('/audits/{audit_id}/samples', {
      path: { audit_id: auditId! },
      query: {
        limit: 100,
        offset: 0,
        ...(toolStatusFilter !== 'all' ? { tool_status: toolStatusFilter } : {}),
        ...(reviewStatusFilter !== 'all' ? { review_status: reviewStatusFilter } : {}),
      },
    }),
    enabled: Boolean(auditId),
  });

  // Set first sample active by default
  useEffect(() => {
    const first = samplePage?.items?.[0];
    if (first && !activeSampleId) {
      setActiveSampleId(first.sample_id);
    }
  }, [samplePage, activeSampleId]);

  // Fetch detailed data for the active sample
  const { data: sampleDetail, error: detailError } = useQuery({
    queryKey: queryKeys.auditSample(auditId!, activeSampleId!),
    queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', {
      path: { audit_id: auditId!, sample_id: activeSampleId! },
    }),
    enabled: Boolean(auditId && activeSampleId),
  });

  // Fetch full sample ground-truth from dataset
  const { data: rawSample, error: rawSampleError } = useQuery({
    queryKey: queryKeys.datasetSample(
      sampleDetail?.dataset_id ?? 'default',
      activeSampleId ?? 'default',
      sampleDetail?.summary.sample_revision,
    ),
    queryFn: () =>
      apiGet('/datasets/{dataset_id}/samples/{sample_id}', {
        path: { dataset_id: sampleDetail!.dataset_id, sample_id: activeSampleId! },
        query: sampleDetail?.summary.sample_revision ? { revision: sampleDetail.summary.sample_revision } : {},
      }),
    enabled: Boolean(sampleDetail?.dataset_id && activeSampleId),
  });

  const canvasObjects: CanvasObject[] = useMemo(() => {
    if (!rawSample?.objects) return [];
    return rawSample.objects.map((obj) => ({
      object_id: obj.object_id,
      label: obj.label,
      bbox: [
        obj.bbox[0] ?? 1,
        obj.bbox[1] ?? 1,
        obj.bbox[2] ?? 1,
        obj.bbox[3] ?? 1,
      ],
    }));
  }, [rawSample]);

  const findings: Finding[] = useMemo(() => sampleDetail?.findings ?? [], [sampleDetail]);
  const currentReview = sampleDetail?.review;

  // Filtered samples queue
  const filteredQueue = useMemo(() => samplePage?.items ?? [], [samplePage]);
  const findingHistoryQuery = useQuery({
    queryKey: ['finding-reviews', historyFindingId],
    queryFn: () => apiGetAllPages('/findings/{finding_id}/reviews', { path: { finding_id: historyFindingId! }, query: { limit: 100, offset: 0 } }),
    enabled: !!historyFindingId,
  });
  const sampleHistoryQuery = useQuery({
    queryKey: ['sample-reviews', auditId, activeSampleId],
    queryFn: () => apiGetAllPages('/audits/{audit_id}/samples/{sample_id}/reviews', { path: { audit_id: auditId!, sample_id: activeSampleId! }, query: { limit: 100, offset: 0, sample_revision: sampleDetail?.summary.sample_revision ?? 1 } }),
    enabled: !!auditId && !!activeSampleId && !!sampleDetail,
  });

  const currentIndex = filteredQueue.findIndex(
    (item) => item.sample_id === activeSampleId,
  );

  const resizePane = (side: 'left' | 'right', delta: number) => {
    if (side === 'left') {
      const next = Math.min(380, Math.max(200, leftWidth + delta));
      setLeftWidth(persistPaneWidth('left', next));
    } else {
      const next = Math.min(480, Math.max(280, rightWidth - delta));
      setRightWidth(persistPaneWidth('right', next));
    }
  };

  const navigateQueue = useCallback(
    (direction: 'next' | 'prev') => {
      if (filteredQueue.length === 0) return;
      let nextIndex =
        direction === 'next' ? currentIndex + 1 : currentIndex - 1;
      if (nextIndex >= filteredQueue.length) nextIndex = 0;
      if (nextIndex < 0) nextIndex = filteredQueue.length - 1;
      const target = filteredQueue[nextIndex];
      if (target) {
        setActiveSampleId(target.sample_id);
      }
    },
    [filteredQueue, currentIndex],
  );

  // Sample Review Verdict Mutation
  const sampleReviewMutation = useMutation({
    mutationFn: async ({
      decision,
    }: {
      decision: SampleReviewDecision;
    }) => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }
      if (!activeSampleId || !auditId) return;

      const expectedRevision = sampleDetail?.summary.sample_revision ?? 1;
      const expectedReviewVersion = currentReview?.revision ?? 0;

      return apiPut('/audits/{audit_id}/samples/{sample_id}/review', {
        path: { audit_id: auditId, sample_id: activeSampleId },
        body: {
          expected_sample_revision: expectedRevision,
          expected_review_version: expectedReviewVersion,
          decision,
          reviewer_id: activeReviewer,
          comment: reviewComment.trim(),
        },
      });
    },
    onMutate: async ({ decision }) => {
      if (!activeSampleId || !auditId) return undefined;
      const key = queryKeys.auditSample(auditId, activeSampleId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData(key);
      queryClient.setQueryData<SampleAudit>(key, (old) => old ? {
        ...old,
        review: {
          ...old.review,
          audit_id: auditId,
          sample_id: activeSampleId,
          sample_revision: old.summary.sample_revision,
          revision: (old.review?.revision ?? 0) + 1,
          decision,
          reviewer_id: reviewerId,
          comment: reviewComment.trim(),
          updated_at: new Date().toISOString(),
        },
      } : old);
      return { key, previous };
    },
    onError: (error, _variables, context) => {
      if (context) queryClient.setQueryData(context.key, context.previous);
      setReviewError(error instanceof ApiError && error.status === 409
        ? 'The sample changed since it was loaded. Your comment is preserved; reload the latest decision, then reapply if appropriate.'
        : error.message);
    },
    onSuccess: () => {
      setReviewError(null);
      setReviewComment('');
      // Invalidate sample detail and queue
      if (activeSampleId && auditId) {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.auditSample(auditId, activeSampleId),
        });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.auditSamples(auditId),
        });
      }
      // Auto-advance to next sample in queue
      navigateQueue('next');
    },
  });

  // Finding Decision Mutation
  const findingReviewMutation = useMutation({
    mutationFn: async ({
      findingId,
      resolution,
      expectedReviewVersion,
    }: {
      findingId: string;
      resolution: Exclude<FindingResolution, 'pending'>;
      expectedReviewVersion: number;
    }) => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Reviewer ID required');
      }

      return apiPut('/findings/{finding_id}/review', {
        path: { finding_id: findingId },
        body: {
          expected_review_version: expectedReviewVersion,
          resolution,
          reviewer_id: activeReviewer,
          comment: reviewComment.trim(),
        },
      });
    },
    onMutate: async ({ findingId, resolution }) => {
      if (!activeSampleId || !auditId) return undefined;
      const key = queryKeys.auditSample(auditId, activeSampleId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData(key);
      queryClient.setQueryData<SampleAudit>(key, (old) => old ? {
        ...old,
        findings: old.findings.map((finding) => finding.finding_id === findingId ? {
          ...finding,
          review: {
            ...finding.review,
            revision: (finding.review?.revision ?? 0) + 1,
            resolution,
            reviewer_id: reviewerId,
            comment: reviewComment.trim(),
            updated_at: new Date().toISOString(),
          },
        } : finding),
      } : old);
      return { key, previous };
    },
    onError: (error, _variables, context) => {
      if (context) queryClient.setQueryData(context.key, context.previous);
      setReviewError(error instanceof ApiError && error.status === 409
        ? 'A newer finding decision exists. Your comment is preserved; reload the finding, then reapply if appropriate.'
        : error.message);
    },
    onSuccess: () => {
      setReviewError(null);
      if (activeSampleId && auditId) {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.auditSample(auditId, activeSampleId),
        });
      }
    },
  });

  // Global Keyboard Navigation & Review Shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (isTypingTarget(e) || e.ctrlKey || e.metaKey || e.altKey) return;

      const action = resolveReviewKey(e.key, e.shiftKey);
      const focusedFinding = findings.find((finding) => finding.object_id === selectedObjectId);
      if (focusedFinding && ['finding-accept', 'finding-reject', 'finding-unsure'].includes(action ?? '')) {
        const resolution = action === 'finding-accept' ? 'accepted' : action === 'finding-reject' ? 'rejected' : 'unsure';
        e.preventDefault();
        findingReviewMutation.mutate({ findingId: focusedFinding.finding_id, resolution, expectedReviewVersion: focusedFinding.review?.revision ?? 0 });
        return;
      }

      switch (e.key.toLowerCase()) {
        case 'a':
        case 'r':
        case 'n':
          if (!action?.startsWith('sample-')) break;
          e.preventDefault();
          sampleReviewMutation.mutate({ decision: action === 'sample-accept' ? 'accepted' : action === 'sample-reject' ? 'rejected' : 'needs_correction' });
          break;
        case 'arrowright':
        case 'j':
          e.preventDefault();
          navigateQueue('next');
          break;
        case 'arrowleft':
        case 'k':
          e.preventDefault();
          navigateQueue('prev');
          break;
        case 'm':
          e.preventDefault();
          toggleMask();
          break;
        case 'b':
          e.preventDefault();
          toggleBoxes();
          break;
        case 'f':
          e.preventDefault();
          resetView();
          break;
        case '+':
        case '=':
          e.preventDefault();
          setZoom(zoom * 1.25);
          break;
        case '-':
        case '_':
          e.preventDefault();
          setZoom(zoom / 1.25);
          break;
        case '?':
          e.preventDefault();
          setIsKeyboardHelpOpen(true);
          break;
        default:
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    sampleReviewMutation,
    findingReviewMutation,
    findings,
    selectedObjectId,
    navigateQueue,
    toggleMask,
    toggleBoxes,
    resetView,
    setZoom,
    zoom,
  ]);

  return (
    <div className="flex flex-col h-full bg-base overflow-hidden">
      {/* Top Breadcrumb & Status Bar */}
      <div className="h-12 border-b border-border bg-surface px-4 flex items-center justify-between shrink-0 select-none">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<ArrowLeft className="w-4 h-4" />}
            onClick={() => navigate('/audits')}
          >
            Audits
          </Button>

          <div className="h-4 w-px bg-border" />

          <div className="flex items-center gap-2">
            <span className="font-mono text-xs font-semibold text-text">
              {auditId}
            </span>
            <span className="text-2xs font-mono text-text-muted">
              dataset: {audit?.dataset_id} · rulebook: {audit?.rulebook_id} (v
              {audit?.rulebook_version})
            </span>
          </div>
        </div>

        {/* Review Progress pill */}
        <div className="flex items-center gap-3">
          <div className="text-2xs font-mono text-text-muted flex items-center gap-1.5">
            <span>Sample:</span>
            <span className="text-accent font-semibold font-mono">
              {currentIndex + 1} / {filteredQueue.length}
            </span>
          </div>

          <button
            onClick={() => setIsKeyboardHelpOpen(true)}
            title="Keyboard Shortcuts (?)"
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-surface-high border border-border text-text-muted hover:text-text text-2xs font-mono"
          >
            <Keyboard className="w-3 h-3" />
            <span>Shortcuts (?)</span>
          </button>
        </div>
      </div>

      {/* 3-Pane Workstation Layout */}
      {(auditError || queueError || detailError || rawSampleError) && <div role="alert" className="p-2 text-status-fail">{String(auditError ?? queueError ?? detailError ?? rawSampleError)}</div>}
      {reviewError && <div role="alert" className="p-2 text-status-fail">{reviewError} <button onClick={() => { if (activeSampleId && auditId) void queryClient.invalidateQueries({ queryKey: queryKeys.auditSample(auditId, activeSampleId) }); }}>Reload latest</button></div>}
      <div className="flex-1 flex min-h-0">
        {/* Left Pane: Queue (280px) */}
        <aside style={{ width: leftWidth }} className="review-rail border-r border-border bg-surface flex flex-col min-h-0 shrink-0 select-none">
          {/* Filters Bar */}
          <div className="p-3 border-b border-border bg-surface-high/40 space-y-2">
            <div className="flex items-center justify-between text-2xs font-mono text-text-muted">
              <span className="flex items-center gap-1 font-semibold uppercase">
                <Filter className="w-3 h-3" /> Queue Filters
              </span>
              <span>{filteredQueue.length} samples</span>
            </div>

            <div className="grid grid-cols-2 gap-1.5">
              <select
                value={toolStatusFilter}
                onChange={(e) => setToolStatusFilter(e.target.value as 'all' | AuditToolStatus)}
                className="bg-surface border border-border rounded px-1.5 py-1 text-3xs font-mono text-text focus:outline-none focus:border-accent"
              >
                <option value="all">Tool: All</option>
                <option value="flagged">Flagged</option>
                <option value="unsure">Unsure</option>
                <option value="no_issue">No Issue</option>
                <option value="unavailable">Unavailable</option>
              </select>

              <select
                value={reviewStatusFilter}
                onChange={(e) => setReviewStatusFilter(e.target.value as 'all' | AuditReviewStatus)}
                className="bg-surface border border-border rounded px-1.5 py-1 text-3xs font-mono text-text focus:outline-none focus:border-accent"
              >
                <option value="all">Review: All</option>
                <option value="pending_review">Pending</option>
                <option value="accepted">Accepted</option>
                <option value="rejected">Rejected</option>
                <option value="needs_correction">Needs Fix</option>
              </select>
            </div>
          </div>

          {/* Queue List */}
          <div className="flex-1 overflow-y-auto divide-y divide-border/60">
            {isQueueLoading ? (
              <div className="p-8 text-center text-xs text-text-muted">
                <Loader2 className="w-4 h-4 animate-spin mx-auto mb-2 text-accent" />
                Loading queue...
              </div>
            ) : filteredQueue.length === 0 ? (
              <div className="p-8 text-center text-2xs text-text-faint">
                No samples match filters.
              </div>
            ) : (
              filteredQueue.map((item, idx) => {
                const isActive = item.sample_id === activeSampleId;
                const decision = item.review_status;

                let decisionVariant: BadgeVariant = 'pending';
                if (decision === 'accepted') decisionVariant = 'accepted';
                else if (decision === 'rejected') decisionVariant = 'rejected';
                else if (decision === 'needs_correction') decisionVariant = 'needs-fix';

                let toolVariant: BadgeVariant = 'unavailable';
                if (item.tool_status === 'no_issue') toolVariant = 'pass';
                else if (item.tool_status === 'flagged') toolVariant = 'fail';
                else if (item.tool_status === 'unsure') toolVariant = 'unsure';

                return (
                  <div
                    key={item.sample_id}
                    onClick={() => setActiveSampleId(item.sample_id)}
                    className={`p-2.5 cursor-pointer transition-colors ${
                      isActive
                        ? 'bg-surface-high border-l-2 border-accent text-text'
                        : 'hover:bg-surface-high/40 text-text-muted'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono text-xs font-semibold text-text">
                        {item.sample_id}
                      </span>
                      <span className="font-mono text-3xs text-text-faint">
                        #{idx + 1}
                      </span>
                    </div>

                    <div className="flex items-center gap-1.5 mt-1">
                      <Badge variant={toolVariant} size="sm">
                        {item.tool_status}
                      </Badge>
                      <Badge variant={decisionVariant} size="sm">
                        {decision === 'pending_review' ? 'unreviewed' : decision}
                      </Badge>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Queue Bottom Pagination / Navigation */}
          <div className="p-2 border-t border-border bg-surface-high/30 flex items-center justify-between">
            <Button
              size="sm"
              variant="ghost"
              leftIcon={<ChevronLeft className="w-3.5 h-3.5" />}
              onClick={() => navigateQueue('prev')}
              title="Previous sample (← or K)"
            >
              Prev
            </Button>
            <span className="text-3xs font-mono text-text-faint">
              [← / → / J / K]
            </span>
            <Button
              size="sm"
              variant="ghost"
              rightIcon={<ChevronRight className="w-3.5 h-3.5" />}
              onClick={() => navigateQueue('next')}
              title="Next sample (→ or J)"
            >
              Next
            </Button>
          </div>
        </aside>

        <div className="review-splitter" role="separator" aria-label="Resize sample queue" aria-orientation="vertical" tabIndex={0} onKeyDown={(e) => { if (e.key === 'ArrowLeft') resizePane('left', -12); if (e.key === 'ArrowRight') resizePane('left', 12); }} onPointerDown={(e) => { const start = e.clientX; const initial = leftWidth; const move = (ev: PointerEvent) => { const next = Math.min(380, Math.max(200, initial + ev.clientX - start)); setLeftWidth(next); localStorage.setItem('prism.review.leftWidth', String(next)); }; const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); }; window.addEventListener('pointermove', move); window.addEventListener('pointerup', up, { once: true }); }} />

        {/* Center Pane: Interactive Canvas */}
        <div className="flex-1 flex flex-col min-h-0 bg-base-pure relative">
          <ReviewCanvas
            sampleId={activeSampleId ?? undefined}
            imageSize={rawSample?.image_size}
            assets={sampleDetail?.assets ?? rawSample?.assets}
            objects={canvasObjects}
            hiddenObjectIds={hiddenObjectIds}
            findings={findings}
            onSelectObject={setSelectedObjectId}
          />
        </div>

        {/* Right Pane: Findings & Sample Verdict (360px) */}
        <div className="review-splitter" role="separator" aria-label="Resize finding inspector" aria-orientation="vertical" tabIndex={0} onKeyDown={(e) => { if (e.key === 'ArrowLeft') resizePane('right', -12); if (e.key === 'ArrowRight') resizePane('right', 12); }} onPointerDown={(e) => { const start = e.clientX; const initial = rightWidth; const move = (ev: PointerEvent) => { const next = Math.min(480, Math.max(280, initial - (ev.clientX - start))); setRightWidth(next); localStorage.setItem('prism.review.rightWidth', String(next)); }; const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); }; window.addEventListener('pointermove', move); window.addEventListener('pointerup', up, { once: true }); }} />
        <aside style={{ width: rightWidth }} className="review-rail border-l border-border bg-surface flex flex-col min-h-0 shrink-0 select-none">
          {/* Active Sample Header */}
          <div className="p-3 border-b border-border bg-surface-high/40 flex items-center justify-between">
            <div>
              <div className="font-mono font-bold text-sm text-text">
                {activeSampleId}
              </div>
              <div className="text-2xs font-mono text-text-muted">
                Revision {sampleDetail?.summary.sample_revision ?? '—'} · {rawSample?.image_size ? `${rawSample.image_size[0]}×${rawSample.image_size[1]} px` : 'image size unavailable'}
              </div>
            </div>

            {currentReview?.decision && currentReview.decision !== 'pending_review' ? (
              <Badge
                variant={
                  currentReview.decision === 'accepted'
                    ? 'accepted'
                    : currentReview.decision === 'rejected'
                    ? 'rejected'
                    : 'needs-fix'
                }
              >
                {currentReview.decision}
              </Badge>
            ) : (
              <Badge variant="pending">Awaiting Review</Badge>
            )}
          </div>

          {/* Findings List (Scrollable) */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            <details className="object-visibility" open={false}>
              <summary>Objects · {canvasObjects.length}</summary>
              <div>{canvasObjects.map((object) => { const hidden = hiddenObjectIds.includes(object.object_id); return <button type="button" key={object.object_id} onClick={() => setHiddenObjectIds((old) => hidden ? old.filter((id) => id !== object.object_id) : [...old, object.object_id])} onDoubleClick={() => setSelectedObjectId(object.object_id)} title="Toggle box visibility; double-click to select its finding"><span>{hidden ? <EyeOff size={13}/> : <Eye size={13}/>}</span><span>#{object.object_id} {object.label}</span></button>; })}</div>
            </details>
            <div className="flex items-center justify-between">
              <span className="text-2xs font-mono uppercase tracking-wider text-text-muted">
                Machine Findings ({findings.length})
              </span>
              <span className="text-3xs font-mono text-accent">
                Proposals only
              </span>
            </div>

            {findings.length === 0 ? (
              <div className="p-6 text-center text-xs text-text-muted border border-dashed border-border rounded">
                No findings generated for this sample.
              </div>
            ) : (
              findings.map((f: Finding) => {
                const resolution = f.review?.resolution ?? 'pending';
                const isSelected = selectedObjectId !== null && f.object_id === selectedObjectId;

                return (
                  <div
                    key={f.finding_id}
                    className={`p-3 rounded-lg border text-xs transition-all ${
                      isSelected
                        ? 'bg-surface-high border-accent ring-1 ring-accent'
                        : 'bg-surface-high/60 border-border'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-1.5">
                        <Badge
                          variant={
                            f.outcome === 'pass'
                              ? 'pass'
                              : f.outcome === 'fail'
                              ? 'fail'
                              : f.outcome === 'unsure'
                              ? 'unsure'
                              : 'unavailable'
                          }
                          size="sm"
                        >
                          {f.outcome}
                        </Badge>
                        <span className="font-mono text-2xs font-semibold text-text">
                          {f.rule_id}
                        </span>
                        {f.object_id && (
                          <span className="font-mono text-3xs text-accent bg-accent/10 px-1 py-0.2 rounded border border-accent/20">
                            Obj #{f.object_id}
                          </span>
                        )}
                      </div>

                      {/* Finding Resolution Status */}
                      <span className="text-3xs font-mono text-text-faint uppercase">
                        {resolution}
                      </span>
                    </div>

                    <p className="text-text text-2xs leading-relaxed mb-2 font-sans">
                      {f.description}
                    </p>

                    {/* Evidence & measurements */}
                    {f.evidence?.measurements && Object.keys(f.evidence.measurements).length > 0 && (
                      <div className="bg-base/80 p-2 rounded text-3xs font-mono text-text-muted mb-2 overflow-x-auto border border-border/40">
                        {Object.entries(f.evidence.measurements).map(([k, v]) => (
                          <div key={k} className="flex justify-between">
                            <span className="text-text-faint">{k}:</span>
                            <span className="text-text font-medium">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Finding Review Decision Buttons */}
                    <div className="pt-2 border-t border-border/50 flex items-center justify-between">
                      <span className="text-3xs font-mono text-text-faint">
                        Confirm Finding?
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() =>
                            findingReviewMutation.mutate({
                              findingId: f.finding_id,
                              resolution: 'accepted',
                              expectedReviewVersion: f.review?.revision ?? 0,
                            })
                          }
                          className={`px-2 py-0.5 rounded text-3xs font-mono transition-colors border ${
                            resolution === 'accepted'
                              ? 'bg-status-accepted text-base-pure border-transparent font-bold'
                              : 'bg-status-accepted/10 text-status-accepted border-status-accepted/30 hover:bg-status-accepted/20'
                          }`}
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            findingReviewMutation.mutate({
                              findingId: f.finding_id,
                              resolution: 'rejected',
                              expectedReviewVersion: f.review?.revision ?? 0,
                            })
                          }
                          className={`px-2 py-0.5 rounded text-3xs font-mono transition-colors border ${
                            resolution === 'rejected'
                              ? 'bg-status-rejected text-base-pure border-transparent font-bold'
                              : 'bg-status-rejected/10 text-status-rejected border-status-rejected/30 hover:bg-status-rejected/20'
                          }`}
                        >
                          Reject
                        </button>
                        <button type="button" onClick={() => findingReviewMutation.mutate({ findingId: f.finding_id, resolution: 'unsure', expectedReviewVersion: f.review?.revision ?? 0 })}>
                          Unsure
                        </button>
                        <button type="button" onClick={() => setHistoryFindingId(f.finding_id)}>History</button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Sample Final Verdict Toolbar (Bottom of Right Pane) */}
          <div className="p-4 border-t border-border bg-surface-high/40 space-y-3">
            {sampleHistoryQuery.data?.items.map((entry) => <div key={`sample-${entry.revision}`} className="text-3xs text-text-muted">r{entry.revision} · {entry.decision} · {entry.reviewer_id}</div>)}
            {findingHistoryQuery.data?.items.map((entry) => <div key={`finding-${entry.revision}`} className="text-3xs text-text-muted">Finding {entry.resolution} · {entry.reviewer_id}: {entry.comment}</div>)}
            <div className="flex items-center justify-between">
              <span className="text-2xs font-mono uppercase tracking-wider text-text font-semibold">
                Sample Verdict
              </span>
              <span className="text-3xs font-mono text-text-faint">
                Attribution: {reviewerId}
              </span>
            </div>

            {/* Review Comment Input */}
            <div>
              <input
                type="text"
                value={reviewComment}
                onChange={(e) => setReviewComment(e.target.value)}
                placeholder="Optional review rationale / comment..."
                className="w-full bg-base border border-border rounded px-2.5 py-1.5 text-xs font-mono text-text placeholder:text-text-faint focus:outline-none focus:border-accent"
              />
            </div>

            {/* Final Verdict Buttons */}
            <div className="grid grid-cols-3 gap-2">
              <Button
                variant="primary"
                size="sm"
                leftIcon={<CheckCircle2 className="w-3.5 h-3.5" />}
                isLoading={sampleReviewMutation.isPending}
                onClick={() => sampleReviewMutation.mutate({ decision: 'accepted' })}
                title="Accept Sample (Shift+A)"
                className="bg-status-accepted hover:bg-status-accepted/90 text-base-pure font-bold"
              >
                Accept
              </Button>

              <Button
                variant="danger"
                size="sm"
                leftIcon={<XCircle className="w-3.5 h-3.5" />}
                isLoading={sampleReviewMutation.isPending}
                onClick={() => sampleReviewMutation.mutate({ decision: 'rejected' })}
                title="Reject Sample (Shift+R)"
                className="font-bold"
              >
                Reject
              </Button>

              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Wrench className="w-3.5 h-3.5 text-status-needs-fix" />}
                isLoading={sampleReviewMutation.isPending}
                onClick={() => sampleReviewMutation.mutate({ decision: 'needs_correction' })}
                title="Needs Correction (Shift+N)"
                className="text-status-needs-fix border-status-needs-fix/30 hover:bg-status-needs-fix/10 font-bold"
              >
                Fix
              </Button>
            </div>
          </div>
        </aside>
      </div>

      {/* Keyboard Shortcuts Modal */}
      <KeyboardHelpModal
        open={isKeyboardHelpOpen}
        onOpenChange={setIsKeyboardHelpOpen}
      />
    </div>
  );
};
