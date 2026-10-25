import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom';
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
  FileBarChart,
  SkipForward,
  AlertTriangle,
  RefreshCw,
} from 'lucide-react';
import { ApiError, apiGet, apiGetAllPages, apiPost, apiPut } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { useCanvasStore } from '../../store/canvasStore';
import { Badge, type BadgeVariant } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { ReviewCanvas, type CanvasObject } from './ReviewCanvas';
import { FindingCard, type FindingDecision } from './FindingCard';
import { KeyboardHelpModal } from '../../components/ui/KeyboardHelpModal';
import { isTypingTarget } from '../../lib/keyboard';
import { checkLabel, reasonLabel, reviewStatusLabel, toolStatusLabel } from '../../lib/labels';
import { formatTimestamp } from '../../lib/format';
import { RECHECK_IN_PROGRESS_MESSAGE, useImageRecheckStatus } from '../../hooks/useImageRecheckStatus';
import { useDatasetName } from '../../hooks/useDatasetName';
import {
  groupFindings,
  isReasonRequired,
  nextUnreviewedIndex,
  persistPaneWidth,
  readPaneWidth,
  resolveReviewKey,
  reviewProgress,
  rulesWithoutFindings,
} from './reviewWorkspace';
import type { components } from '../../api/generated/openapi';
import type { operations } from '../../api/generated/openapi';

type SampleAudit = components['schemas']['SampleAudit'];
type Finding = components['schemas']['Finding'];
type SampleReviewDecision = components['schemas']['SampleReviewDecision'];
type AuditQueueQuery = NonNullable<operations['listAuditSamples']['parameters']['query']>;
type AuditToolStatus = NonNullable<AuditQueueQuery['tool_status']>;
type AuditReviewStatus = NonNullable<AuditQueueQuery['review_status']>;

const TOOL_VARIANT: Record<string, BadgeVariant> = { no_issue: 'pass', flagged: 'fail', unsure: 'unsure', unavailable: 'unavailable', pending: 'pending' };
const DECISION_VARIANT: Record<string, BadgeVariant> = { accepted: 'accepted', rejected: 'rejected', needs_correction: 'needs-fix', pending_review: 'pending' };
const ALL_FILTERS = { toolStatusFilter: 'all', reviewStatusFilter: 'all' } as const;

export const AuditReviewWorkspace: React.FC = () => {
  const { auditId } = useParams<{ auditId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const { toggleMask, toggleBoxes, resetView, zoom, setZoom, setSelectedInstanceId } = useCanvasStore();
  const commentRef = useRef<HTMLTextAreaElement>(null);
  const datasetName = useDatasetName();
  const [searchParams] = useSearchParams();

  // `?sample=` lets the report and other screens deep-link to one sample.
  const [activeSampleId, setActiveSampleId] = useState<string | null>(() => searchParams.get('sample'));
  const [historicalAttemptId, setHistoricalAttemptId] = useState<string | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [hiddenObjectIds, setHiddenObjectIds] = useState<number[]>([]);
  const [reviewComment, setReviewComment] = useState<string>('');
  const [isKeyboardHelpOpen, setIsKeyboardHelpOpen] = useState<boolean>(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  // Separate from reviewError: loading the other recheck's new attempt must not hide it.
  const [recheckNotice, setRecheckNotice] = useState<string | null>(null);
  const [historyFindingId, setHistoryFindingId] = useState<string | null>(null);
  const [correctionPrompt, setCorrectionPrompt] = useState<{ datasetId: string; sampleId: string; auditId: string } | null>(null);
  const [leftWidth, setLeftWidth] = useState(() => readPaneWidth('left', 264));
  const [rightWidth, setRightWidth] = useState(() => readPaneWidth('right', 380));

  const [toolStatusFilter, setToolStatusFilter] = useState<'all' | AuditToolStatus>('all');
  const [reviewStatusFilter, setReviewStatusFilter] = useState<'all' | AuditReviewStatus>('all');

  const { data: audit, error: auditError } = useQuery({
    queryKey: queryKeys.audit(auditId!),
    queryFn: () => apiGet('/audits/{audit_id}', { path: { audit_id: auditId! } }),
    enabled: Boolean(auditId),
  });

  // The confirmed rulebook version this audit pinned — used to list rules with no finding.
  const { data: rulebook } = useQuery({
    queryKey: queryKeys.rulebook(audit?.rulebook_id ?? '', audit?.rulebook_version),
    queryFn: () => apiGet('/rulebooks/{rulebook_id}', { path: { rulebook_id: audit!.rulebook_id }, query: { version: audit!.rulebook_version } }),
    enabled: Boolean(audit?.rulebook_id),
  });

  const queueQuery = (filters: { toolStatusFilter: 'all' | AuditToolStatus; reviewStatusFilter: 'all' | AuditReviewStatus }) => ({
    queryKey: queryKeys.auditSamples(auditId!, { ...filters, include_rechecks: true }),
    queryFn: () => apiGetAllPages('/audits/{audit_id}/samples', {
      path: { audit_id: auditId! },
      query: {
        limit: 100,
        offset: 0,
        include_rechecks: true,
        ...(filters.toolStatusFilter !== 'all' ? { tool_status: filters.toolStatusFilter } : {}),
        ...(filters.reviewStatusFilter !== 'all' ? { review_status: filters.reviewStatusFilter } : {}),
      },
    }),
    enabled: Boolean(auditId),
    refetchInterval: 5000,
  });
  const { data: samplePage, isLoading: isQueueLoading, error: queueError } = useQuery(queueQuery({ toolStatusFilter, reviewStatusFilter }));
  // Unfiltered queue drives overall progress; it shares the cache entry when no filter is set.
  const { data: fullQueue } = useQuery(queueQuery(ALL_FILTERS));

  useEffect(() => {
    const first = samplePage?.items?.[0];
    if (first && !activeSampleId) setActiveSampleId(first.sample_id);
  }, [samplePage, activeSampleId]);

  // A new sample starts with a clean selection and comment.
  useEffect(() => {
    setHistoricalAttemptId(null);
    setSelectedFindingId(null);
    setSelectedInstanceId(null);
    setHistoryFindingId(null);
    setReviewComment('');
    setReviewError(null);
    setRecheckNotice(null);
    setHiddenObjectIds([]);
  }, [activeSampleId, setSelectedInstanceId]);

  const latestDetailKey = [...queryKeys.auditSample(auditId!, activeSampleId!), 'latest'];
  const latestDetail = useQuery({
    queryKey: latestDetailKey,
    queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', {
      path: { audit_id: auditId!, sample_id: activeSampleId! },
      query: { include_rechecks: true },
    }),
    enabled: Boolean(auditId && activeSampleId),
    refetchInterval: 5000,
  });
  const historicalDetail = useQuery({
    queryKey: queryKeys.auditSample(historicalAttemptId ?? '', activeSampleId!),
    queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', {
      path: { audit_id: historicalAttemptId!, sample_id: activeSampleId! },
    }),
    enabled: Boolean(historicalAttemptId && activeSampleId),
  });
  const displayedDetail = historicalAttemptId ? historicalDetail : latestDetail;
  const sampleDetail = displayedDetail.data;
  const detailError = displayedDetail.error;
  const isDetailLoading = displayedDetail.isLoading;
  const attemptRunning = ['queued', 'running'].includes(latestDetail.data?.summary.processing_status ?? 'queued');
  // Survives refresh: blocked while any attempt of this image has an unfinished job.
  const recheckStatus = useImageRecheckStatus(auditId, activeSampleId);

  // A new attempt has new evidence and needs its own decision and reason.
  useEffect(() => {
    setSelectedFindingId(null);
    setHistoryFindingId(null);
    setReviewComment('');
    setReviewError(null);
  }, [sampleDetail?.audit_id]);

  const recheckMutation = useMutation({
    mutationFn: async () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Set your reviewer name first (top right).'); }
      if (!auditId || !activeSampleId || !latestDetail.data) throw new Error('Load the image first.');
      return apiPost('/audits/{audit_id}/rechecks', {
        path: { audit_id: auditId },
        body: {
          sample_ids: [activeSampleId],
          sample_revisions: { [activeSampleId]: latestDetail.data.summary.sample_revision },
          reviewer_id: reviewerId.trim(),
        },
      });
    },
    onSuccess: (result) => {
      setHistoricalAttemptId(null);
      recheckStatus.track(result.job.job_id);
      setReviewError(null);
      setRecheckNotice(null);
      return queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'RECHECK_ALREADY_RUNNING') {
        setRecheckNotice(RECHECK_IN_PROGRESS_MESSAGE);
        recheckStatus.refresh();
      } else {
        setReviewError(error instanceof ApiError && error.code === 'STALE_SAMPLE_REVISION'
          ? 'This image has a newer correction. Open Correct and re-check the saved revision there.'
          : error.message);
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.audit(auditId!) });
    },
  });
  const canReview = !!sampleDetail && !latestDetail.error && !historicalAttemptId
    && ['completed', 'failed'].includes(sampleDetail.summary.processing_status) && !recheckMutation.isPending;

  useEffect(() => {
    const root = latestDetail.data?.workspace_audit_id;
    if (root && root !== auditId && activeSampleId) {
      navigate(`/audits/${root}/review?sample=${encodeURIComponent(activeSampleId)}`, { replace: true });
    }
  }, [latestDetail.data?.workspace_audit_id, auditId, activeSampleId, navigate]);

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
      bbox: [obj.bbox[0] ?? 1, obj.bbox[1] ?? 1, obj.bbox[2] ?? 1, obj.bbox[3] ?? 1],
    }));
  }, [rawSample]);

  const findings: Finding[] = useMemo(() => sampleDetail?.findings ?? [], [sampleDetail]);
  const { attention, passed } = useMemo(() => groupFindings(findings), [findings]);
  const notEvaluated = useMemo(() => rulesWithoutFindings(rulebook?.rules, findings), [rulebook, findings]);
  const currentReview = sampleDetail?.review;
  const filteredQueue = useMemo(() => samplePage?.items ?? [], [samplePage]);
  const progress = useMemo(() => reviewProgress(fullQueue?.items ?? []), [fullQueue]);

  const findingHistoryQuery = useQuery({
    queryKey: queryKeys.findingReviews(historyFindingId ?? ''),
    queryFn: () => apiGetAllPages('/findings/{finding_id}/reviews', { path: { finding_id: historyFindingId! }, query: { limit: 100, offset: 0 } }),
    enabled: !!historyFindingId,
  });
  const sampleHistoryQuery = useQuery({
    queryKey: queryKeys.sampleReviews(sampleDetail?.audit_id ?? '', activeSampleId ?? '', sampleDetail?.summary.sample_revision ?? 1),
    queryFn: () => apiGetAllPages('/audits/{audit_id}/samples/{sample_id}/reviews', { path: { audit_id: sampleDetail!.audit_id, sample_id: activeSampleId! }, query: { limit: 100, offset: 0, sample_revision: sampleDetail?.summary.sample_revision ?? 1 } }),
    enabled: !!auditId && !!activeSampleId && !!sampleDetail,
  });

  const currentIndex = filteredQueue.findIndex((item) => item.sample_id === activeSampleId);

  const resizePane = (side: 'left' | 'right', delta: number) => {
    if (side === 'left') setLeftWidth((width) => persistPaneWidth('left', width + delta));
    else setRightWidth((width) => persistPaneWidth('right', width - delta));
  };

  const navigateQueue = useCallback(
    (direction: 'next' | 'prev') => {
      if (filteredQueue.length === 0) return;
      let nextIndex = direction === 'next' ? currentIndex + 1 : currentIndex - 1;
      if (nextIndex >= filteredQueue.length) nextIndex = 0;
      if (nextIndex < 0) nextIndex = filteredQueue.length - 1;
      const target = filteredQueue[nextIndex];
      if (target) setActiveSampleId(target.sample_id);
    },
    [filteredQueue, currentIndex],
  );

  const jumpToNextUnreviewed = useCallback(() => {
    const index = nextUnreviewedIndex(filteredQueue, currentIndex);
    const target = index >= 0 ? filteredQueue[index] : undefined;
    if (target) setActiveSampleId(target.sample_id);
    else setReviewError('Every sample in this view has a decision.');
  }, [filteredQueue, currentIndex]);

  /** Enforce the "every override needs a reason" rule before any write. */
  const ensureReason = useCallback((required: boolean, what: string): boolean => {
    if (!required || reviewComment.trim()) return true;
    setReviewError(`Type a reason in the comment box before you ${what}. Every override must be traceable.`);
    commentRef.current?.focus();
    return false;
  }, [reviewComment]);

  const sampleReviewMutation = useMutation({
    mutationFn: async ({ decision }: { decision: SampleReviewDecision }) => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Set your reviewer name first (top right).');
      }
      if (!activeSampleId || !auditId) return;
      return apiPut('/audits/{audit_id}/samples/{sample_id}/review', {
        path: { audit_id: sampleDetail!.audit_id, sample_id: activeSampleId },
        body: {
          expected_sample_revision: sampleDetail?.summary.sample_revision ?? 1,
          expected_review_version: currentReview?.revision ?? 0,
          decision,
          reviewer_id: activeReviewer,
          comment: reviewComment.trim(),
        },
      });
    },
    onMutate: async ({ decision }) => {
      if (!activeSampleId || !auditId) return undefined;
      const key = [...queryKeys.auditSample(auditId, activeSampleId), 'latest'];
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData(key);
      queryClient.setQueryData<SampleAudit>(key, (old) => old ? {
        ...old,
        review: {
          ...old.review,
          audit_id: old.audit_id,
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
    onSuccess: (_result, { decision }) => {
      setReviewError(null);
      setReviewComment('');
      if (activeSampleId && auditId) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.auditSample(auditId, activeSampleId) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.auditSamples(auditId) });
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
      setCorrectionPrompt(decision === 'needs_correction' && _result && audit
        ? { datasetId: audit.dataset_id, sampleId: _result.sample_id, auditId: _result.audit_id }
        : null);
      navigateQueue('next');
    },
  });

  const findingReviewMutation = useMutation({
    mutationFn: async ({ findingId, resolution, expectedReviewVersion }: { findingId: string; resolution: FindingDecision; expectedReviewVersion: number }) => {
      const activeReviewer = reviewerId?.trim();
      if (!activeReviewer) {
        openPrompt();
        throw new Error('Set your reviewer name first (top right).');
      }
      return apiPut('/findings/{finding_id}/review', {
        path: { finding_id: findingId },
        body: { expected_review_version: expectedReviewVersion, resolution, reviewer_id: activeReviewer, comment: reviewComment.trim() },
      });
    },
    onMutate: async ({ findingId, resolution }) => {
      if (!activeSampleId || !auditId) return undefined;
      const key = [...queryKeys.auditSample(auditId, activeSampleId), 'latest'];
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
    onSuccess: (_result, { findingId }) => {
      setReviewError(null);
      // Each override needs its own reason, so the comment does not carry over.
      setReviewComment('');
      if (activeSampleId && auditId) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.auditSample(auditId, activeSampleId) });
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.findingReviews(findingId) });
    },
  });

  const decideFinding = useCallback((finding: Finding, resolution: FindingDecision) => {
    if (!canReview || findingReviewMutation.isPending || sampleReviewMutation.isPending) return;
    if (!ensureReason(isReasonRequired({ kind: 'finding', resolution }), 'override the tool')) return;
    findingReviewMutation.mutate({ findingId: finding.finding_id, resolution, expectedReviewVersion: finding.review?.revision ?? 0 });
  }, [canReview, ensureReason, findingReviewMutation, sampleReviewMutation.isPending]);

  const decideSample = useCallback((decision: SampleReviewDecision) => {
    if (!canReview || findingReviewMutation.isPending || sampleReviewMutation.isPending) return;
    const verb = decision === 'rejected' ? 'reject this sample' : 'send this sample for correction';
    if (!ensureReason(isReasonRequired({ kind: 'sample', decision }), verb)) return;
    sampleReviewMutation.mutate({ decision });
  }, [canReview, ensureReason, sampleReviewMutation, findingReviewMutation.isPending]);

  const selectFinding = useCallback((finding: Finding) => {
    setSelectedFindingId(finding.finding_id);
    const objectId = finding.object_id ?? null;
    setSelectedInstanceId(objectId);
  }, [setSelectedInstanceId]);

  // Selecting a box on the canvas focuses the most relevant finding for that object.
  const selectObject = useCallback((objectId: number | null) => {
    if (objectId === null) return;
    const match = [...attention, ...passed].find((finding) => finding.object_id === objectId);
    if (match) setSelectedFindingId(match.finding_id);
  }, [attention, passed]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (isTypingTarget(e) || e.ctrlKey || e.metaKey || e.altKey) return;
      const action = resolveReviewKey(e.key, e.shiftKey);
      const focusedFinding = findings.find((finding) => finding.finding_id === selectedFindingId);
      if (focusedFinding && (action === 'finding-accept' || action === 'finding-reject' || action === 'finding-unsure')) {
        e.preventDefault();
        decideFinding(focusedFinding, action === 'finding-accept' ? 'accepted' : action === 'finding-reject' ? 'rejected' : 'unsure');
        return;
      }
      switch (action) {
        case 'sample-accept': e.preventDefault(); decideSample('accepted'); break;
        case 'sample-reject': e.preventDefault(); decideSample('rejected'); break;
        case 'sample-correction': e.preventDefault(); decideSample('needs_correction'); break;
        case 'next': e.preventDefault(); navigateQueue('next'); break;
        case 'previous': e.preventDefault(); navigateQueue('prev'); break;
        case 'mask': e.preventDefault(); toggleMask(); break;
        case 'boxes': e.preventDefault(); toggleBoxes(); break;
        case 'fit': e.preventDefault(); resetView(); break;
        case 'help': e.preventDefault(); setIsKeyboardHelpOpen(true); break;
        default:
          if (e.key === '+' || e.key === '=') { e.preventDefault(); setZoom(zoom * 1.25); }
          if (e.key === '-' || e.key === '_') { e.preventDefault(); setZoom(zoom / 1.25); }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [decideFinding, decideSample, findings, selectedFindingId, navigateQueue, toggleMask, toggleBoxes, resetView, setZoom, zoom]);

  const renderFinding = (f: Finding) => (
    <FindingCard
      key={f.finding_id}
      finding={f}
      selected={selectedFindingId === f.finding_id}
      onSelect={() => selectFinding(f)}
      onDecide={(resolution) => decideFinding(f, resolution)}
      historyOpen={historyFindingId === f.finding_id}
      onToggleHistory={() => setHistoryFindingId((id) => (id === f.finding_id ? null : f.finding_id))}
      history={historyFindingId === f.finding_id ? findingHistoryQuery.data?.items : undefined}
      historyLoading={historyFindingId === f.finding_id && findingHistoryQuery.isLoading}
      disabled={!canReview || findingReviewMutation.isPending || sampleReviewMutation.isPending}
    />
  );

  const reasonNeededHint = reviewComment.trim() ? null : 'Required to override a finding, reject, or send for correction.';
  const loadError = auditError ?? queueError ?? detailError ?? rawSampleError;

  return (
    <div className="flex flex-col h-full bg-base overflow-hidden">
      {/* Header: audit identity, progress and shortcuts */}
      <div className="review-header">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />} onClick={() => navigate('/audits')}>
            Audits
          </Button>
          <div className="h-4 w-px bg-border" />
          <div className="min-w-0">
            <div className="font-mono text-xs font-semibold text-text truncate">{auditId}</div>
            <div className="text-2xs font-mono text-text-muted truncate">
              dataset {datasetName(audit?.dataset_id)} · rulebook {audit?.rulebook_id ?? '…'} v{audit?.rulebook_version ?? '…'}
            </div>
          </div>
        </div>

        <div className="review-progress" aria-label="Review progress">
          <div className="review-progress-text">
            <b>{progress.reviewed}</b> of <b>{progress.total}</b> reviewed
            {progress.pending > 0 && <span> · {progress.pending} left</span>}
          </div>
          <div className="review-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.reviewed}>
            <span style={{ width: `${Math.round(progress.fraction * 100)}%` }} />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" leftIcon={<SkipForward className="w-3.5 h-3.5" />} onClick={jumpToNextUnreviewed} disabled={progress.pending === 0}>
            Next unreviewed
          </Button>
          <Link className="review-header-link" to={`/audits/${auditId}/report`}><FileBarChart size={13} /> Report</Link>
          <button onClick={() => setIsKeyboardHelpOpen(true)} title="Keyboard shortcuts (?)" className="review-header-link">
            <Keyboard size={13} /> <span>?</span>
          </button>
        </div>
      </div>

      {loadError && <div role="alert" className="review-alert">{String(loadError)}</div>}
      {reviewError && (
        <div role="alert" className="review-alert">
          <AlertTriangle size={14} /> <span>{reviewError}</span>
          <button onClick={() => { setReviewError(null); if (activeSampleId && auditId) void queryClient.invalidateQueries({ queryKey: queryKeys.auditSample(auditId, activeSampleId) }); }}>Reload latest</button>
        </div>
      )}
      {recheckNotice && (
        <div role="alert" className="review-alert">
          <AlertTriangle size={14} /> <span>{recheckNotice}</span>
          <button onClick={() => setRecheckNotice(null)} aria-label="Dismiss">×</button>
        </div>
      )}
      {correctionPrompt && (
        <div role="status" className="review-notice">
          <Wrench size={14} /> <span>{correctionPrompt.sampleId} was sent for correction.</span>
          <Link to={`/corrections?dataset=${encodeURIComponent(correctionPrompt.datasetId)}&sample=${encodeURIComponent(correctionPrompt.sampleId)}&audit=${encodeURIComponent(correctionPrompt.auditId)}`}>Fix its annotations now →</Link>
          <button onClick={() => setCorrectionPrompt(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        {/* Left pane: sample queue */}
        <aside style={{ width: leftWidth }} className="review-rail border-r border-border bg-surface flex flex-col min-h-0 shrink-0 select-none">
          <div className="p-3 border-b border-border space-y-2">
            <div className="flex items-center justify-between text-2xs font-mono text-text-muted">
              <span className="flex items-center gap-1 font-semibold uppercase"><Filter className="w-3 h-3" /> Queue</span>
              <span>{filteredQueue.length} samples</span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              <select aria-label="Filter by tool result" value={toolStatusFilter} onChange={(e) => setToolStatusFilter(e.target.value as 'all' | AuditToolStatus)}>
                <option value="all">Tool: all</option>
                <option value="flagged">Flagged</option>
                <option value="unsure">Unsure</option>
                <option value="no_issue">No issue found</option>
                <option value="unavailable">Could not check</option>
              </select>
              <select aria-label="Filter by review decision" value={reviewStatusFilter} onChange={(e) => setReviewStatusFilter(e.target.value as 'all' | AuditReviewStatus)}>
                <option value="all">Review: all</option>
                <option value="pending_review">Awaiting review</option>
                <option value="accepted">Accepted</option>
                <option value="rejected">Rejected</option>
                <option value="needs_correction">Needs correction</option>
              </select>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-border/60">
            {isQueueLoading ? (
              <div className="p-8 text-center text-xs text-text-muted">
                <Loader2 className="w-4 h-4 animate-spin mx-auto mb-2 text-accent" /> Loading queue…
              </div>
            ) : filteredQueue.length === 0 ? (
              <div className="p-8 text-center text-2xs text-text-faint">No samples match these filters.</div>
            ) : (
              filteredQueue.map((item, idx) => {
                const isActive = item.sample_id === activeSampleId;
                const counts = item.finding_counts;
                return (
                  <button
                    type="button"
                    key={item.sample_id}
                    onClick={() => setActiveSampleId(item.sample_id)}
                    aria-current={isActive ? 'true' : undefined}
                    className={`queue-item${isActive ? ' is-active' : ''}`}
                  >
                    <span className="queue-item-top">
                      <span className="font-mono text-xs font-semibold text-text">{item.sample_id}</span>
                      <span className="font-mono text-3xs text-text-faint">#{idx + 1}</span>
                    </span>
                    <span className="flex items-center gap-1.5 flex-wrap">
                      <Badge variant={TOOL_VARIANT[item.tool_status] ?? 'unavailable'} size="sm">{toolStatusLabel(item.tool_status)}</Badge>
                      <Badge variant={DECISION_VARIANT[item.review_status] ?? 'pending'} size="sm">{reviewStatusLabel(item.review_status)}</Badge>
                    </span>
                    {(counts.fail > 0 || counts.unsure > 0 || counts.unavailable > 0) && (
                      <span className="queue-item-counts">
                        {counts.fail > 0 && <span className="is-fail">{counts.fail} fail</span>}
                        {counts.unsure > 0 && <span className="is-unsure">{counts.unsure} unsure</span>}
                        {counts.unavailable > 0 && <span>{counts.unavailable} n/a</span>}
                      </span>
                    )}
                  </button>
                );
              })
            )}
          </div>

          <div className="p-2 border-t border-border flex items-center justify-between">
            <Button size="sm" variant="ghost" leftIcon={<ChevronLeft className="w-3.5 h-3.5" />} onClick={() => navigateQueue('prev')} title="Previous sample (← or K)">Prev</Button>
            <span className="text-3xs font-mono text-text-faint">{currentIndex + 1} / {filteredQueue.length}</span>
            <Button size="sm" variant="ghost" rightIcon={<ChevronRight className="w-3.5 h-3.5" />} onClick={() => navigateQueue('next')} title="Next sample (→ or J)">Next</Button>
          </div>
        </aside>

        <PaneSplitter label="Resize sample queue" onResize={(delta) => resizePane('left', delta)} />

        {/* Center pane: image canvas */}
        <div className="flex-1 flex flex-col min-h-0 bg-base-pure relative">
          <ReviewCanvas
            sampleId={activeSampleId ?? undefined}
            imageSize={rawSample?.image_size}
            assets={sampleDetail?.assets ?? rawSample?.assets}
            objects={canvasObjects}
            hiddenObjectIds={hiddenObjectIds}
            findings={findings}
            onSelectObject={selectObject}
          />
        </div>

        <PaneSplitter label="Resize finding inspector" onResize={(delta) => resizePane('right', delta)} />

        {/* Right pane: checklist and sample decision */}
        <aside style={{ width: rightWidth }} className="review-rail border-l border-border bg-surface flex flex-col min-h-0 shrink-0">
          <div className="p-3 border-b border-border flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="font-mono font-bold text-sm text-text truncate">{activeSampleId ?? '—'}</div>
              <div className="text-2xs font-mono text-text-muted">
                Revision {sampleDetail?.summary.sample_revision ?? '—'} · {rawSample?.image_size ? `${rawSample.image_size[0]}×${rawSample.image_size[1]} px` : 'image size unavailable'}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {sampleDetail && activeSampleId && (
                <Link className="review-header-link" title="Open this sample in the correction editor" to={`/corrections?dataset=${encodeURIComponent(sampleDetail.dataset_id)}&sample=${encodeURIComponent(activeSampleId)}&audit=${encodeURIComponent(sampleDetail.audit_id)}`}>
                  <Wrench size={12} /> Correct
                </Link>
              )}
              <Badge variant={DECISION_VARIANT[currentReview?.decision ?? 'pending_review'] ?? 'pending'}>{reviewStatusLabel(currentReview?.decision ?? 'pending_review')}</Badge>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            <section className="checklist-section" aria-label="Image rechecks">
              <Button size="sm" variant="secondary" leftIcon={<RefreshCw size={13} />}
                onClick={() => recheckMutation.mutate()} isLoading={recheckMutation.isPending}
                disabled={!latestDetail.data || attemptRunning || !recheckStatus.canRecheck || !!historicalAttemptId || sampleReviewMutation.isPending || findingReviewMutation.isPending || !audit || !['completed', 'failed', 'cancelled'].includes(audit.status)}>
                Re-check this image
              </Button>
              <p className="text-2xs text-text-muted mt-2">
                {recheckStatus.state === 'loading' ? "Loading this image's recheck status…"
                  : recheckStatus.state === 'error' ? null
                  : recheckStatus.activeJob?.status === 'cancelling' ? 'The current recheck is being cancelled. You can re-check once it stops.'
                  : attemptRunning || recheckStatus.state === 'active' ? 'Checking this image. Its new results will appear here and need a fresh review.'
                  : latestDetail.data?.summary.processing_status === 'cancelled'
                    ? 'The latest attempt was cancelled. Re-check this image to try again; earlier results remain in history.'
                  : 'Rechecks update this workspace. Previous results and decisions stay in attempt history.'}
              </p>
              {recheckStatus.state === 'error' && (
                <div role="alert" className="text-2xs text-status-fail mt-2">
                  Could not load this image's recheck status, so re-checking is paused.{' '}
                  <button type="button" className="underline" onClick={recheckStatus.refresh}>Retry</button>
                </div>
              )}
              {(latestDetail.data?.attempts?.length ?? 0) > 1 && (
                <label className="block text-2xs mt-2">Attempt history
                  <select aria-label="Attempt history" className="w-full mt-1" value={historicalAttemptId ?? ''}
                    disabled={sampleReviewMutation.isPending || findingReviewMutation.isPending || recheckMutation.isPending}
                    onChange={(event) => setHistoricalAttemptId(event.target.value || null)}>
                    <option value="">Latest attempt</option>
                    {latestDetail.data!.attempts!.slice(1).map((attempt) => (
                      <option key={attempt.audit_id} value={attempt.audit_id}>
                        {formatTimestamp(attempt.created_at)} · r{attempt.sample_revision} · {attempt.processing_status}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {historicalAttemptId && <p className="text-2xs text-status-unsure mt-2">Previous attempt — read only. Select Latest attempt to review.</p>}
            </section>
            {isDetailLoading && <p className="text-xs text-text-muted"><Loader2 className="inline w-3 h-3 animate-spin" /> Loading findings…</p>}

            {sampleDetail?.failures && sampleDetail.failures.length > 0 && (
              <section className="checklist-section is-warning">
                <h3>Processing problems ({sampleDetail.failures.length})</h3>
                {sampleDetail.failures.map((failure) => (
                  <p key={failure.failure_id} className="text-2xs">{humanStage(failure.stage)}: {reasonLabel(failure.reason)} <span className="text-text-faint">— {failure.detail}</span></p>
                ))}
              </section>
            )}

            <details className="object-visibility">
              <summary>Objects on image · {canvasObjects.length}</summary>
              <div>{canvasObjects.map((object) => {
                const hidden = hiddenObjectIds.includes(object.object_id);
                return (
                  <button type="button" key={object.object_id} onClick={() => setHiddenObjectIds((old) => hidden ? old.filter((id) => id !== object.object_id) : [...old, object.object_id])} onDoubleClick={() => selectObject(object.object_id)} title="Toggle box visibility; double-click to select its finding">
                    <span>{hidden ? <EyeOff size={13} /> : <Eye size={13} />}</span><span>#{object.object_id} {object.label}</span>
                  </button>
                );
              })}</div>
            </details>

            <p className="checklist-hint">The tool proposes; you decide. Agree with each result or override it — overrides need a reason.</p>

            <section className="checklist-section" aria-label="Findings that need attention">
              <h3>Needs attention <span>{attention.length}</span></h3>
              {attention.length === 0
                ? <p className="checklist-empty">{findings.length ? 'Nothing flagged — check the pre-ticked results below.' : 'No findings for this sample.'}</p>
                : attention.map(renderFinding)}
            </section>

            {passed.length > 0 && (
              <details className="checklist-section" open={attention.length === 0}>
                <summary><h3>Pre-ticked by the tool (passed) <span>{passed.length}</span></h3></summary>
                {passed.map(renderFinding)}
              </details>
            )}

            {notEvaluated.length > 0 && (
              <details className="checklist-section">
                <summary><h3>Rules with no result for this sample <span>{notEvaluated.length}</span></h3></summary>
                <p className="checklist-empty">These confirmed rules produced no finding here. Check them by eye; record anything you spot in the sample comment.</p>
                <ul className="not-evaluated">
                  {notEvaluated.map((rule) => (
                    <li key={rule.rule_id}>
                      <b className="font-mono">{rule.rule_id}</b> {rule.description}
                      <small>{rule.check_name ? checkLabel(rule.check_name) : 'No automatic check'}{rule.source_sentence ? ` · “${rule.source_sentence}”` : ''}</small>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {sampleHistoryQuery.data && sampleHistoryQuery.data.items.length > 0 && (
              <details className="checklist-section">
                <summary><h3>Sample decision history <span>{sampleHistoryQuery.data.items.length}</span></h3></summary>
                <ol className="finding-history">
                  {sampleHistoryQuery.data.items.map((entry) => (
                    <li key={entry.revision}>
                      <b>{reviewStatusLabel(entry.decision ?? 'pending_review')}</b> · {entry.reviewer_id} · {formatTimestamp(entry.updated_at)}
                      {entry.comment && <span> — “{entry.comment}”</span>}
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </div>

          {/* Sample decision */}
          <div className="p-3 border-t border-border space-y-2">
            <div className="flex items-center justify-between">
              <label htmlFor="review-comment" className="text-2xs font-mono uppercase tracking-wider text-text font-semibold">Reason / comment</label>
              <span className="text-3xs font-mono text-text-faint">as {reviewerId || 'no reviewer set'}</span>
            </div>
            <textarea
              id="review-comment"
              ref={commentRef}
              rows={2}
              value={reviewComment}
              onChange={(e) => { setReviewComment(e.target.value); if (reviewError?.startsWith('Type a reason')) setReviewError(null); }}
              placeholder="Why? e.g. “Box misses the left arm” — required for overrides, rejects and corrections"
              className="review-comment"
              aria-describedby="review-comment-hint"
            />
            {reasonNeededHint && <p id="review-comment-hint" className="text-3xs text-text-faint">{reasonNeededHint}</p>}
            <div className="grid grid-cols-3 gap-2">
              <Button variant="primary" size="sm" leftIcon={<CheckCircle2 className="w-3.5 h-3.5" />} disabled={!canReview || findingReviewMutation.isPending} isLoading={sampleReviewMutation.isPending} onClick={() => decideSample('accepted')} title="Accept sample (Shift+A)" className="bg-status-accepted hover:bg-status-accepted/90 text-base-pure font-bold">
                Accept
              </Button>
              <Button variant="danger" size="sm" leftIcon={<XCircle className="w-3.5 h-3.5" />} disabled={!canReview || findingReviewMutation.isPending} isLoading={sampleReviewMutation.isPending} onClick={() => decideSample('rejected')} title="Reject sample — reason required (Shift+R)" className="font-bold">
                Reject
              </Button>
              <Button variant="secondary" size="sm" leftIcon={<Wrench className="w-3.5 h-3.5 text-status-needs-fix" />} disabled={!canReview || findingReviewMutation.isPending} isLoading={sampleReviewMutation.isPending} onClick={() => decideSample('needs_correction')} title="Needs correction — reason required (Shift+N)" className="text-status-needs-fix border-status-needs-fix/30 hover:bg-status-needs-fix/10 font-bold">
                Needs fix
              </Button>
            </div>
          </div>
        </aside>
      </div>

      <KeyboardHelpModal open={isKeyboardHelpOpen} onOpenChange={setIsKeyboardHelpOpen} />
    </div>
  );
};

function humanStage(stage: string): string {
  return stage.charAt(0).toUpperCase() + stage.slice(1).replace(/_/g, ' ');
}

/** Draggable, keyboard-accessible divider between review panes. */
const PaneSplitter: React.FC<{ label: string; onResize: (delta: number) => void }> = ({ label, onResize }) => (
  <div
    className="review-splitter"
    role="separator"
    aria-label={label}
    aria-orientation="vertical"
    tabIndex={0}
    onKeyDown={(e) => { if (e.key === 'ArrowLeft') onResize(-12); if (e.key === 'ArrowRight') onResize(12); }}
    onPointerDown={(e) => {
      let last = e.clientX;
      const move = (ev: PointerEvent) => { onResize(ev.clientX - last); last = ev.clientX; };
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up, { once: true });
    }}
  />
);
