import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
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
import { useDisplayNames } from '../../hooks/useDisplayNames';
import { auditLabel, rulebookLabel } from '../../lib/names';
import { AuditNameText, TechnicalId } from '../../components/ui/RecordName';
import { TitleEditor } from '../names/TitleEditor';
import { useCanvasStore } from '../../store/canvasStore';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { ReviewCanvas, type CanvasObject } from './ReviewCanvas';
import { correctionPreview } from './reviewFeedback';
import { AttemptTrail } from './AttemptTrail';
import { attemptTrail } from './attemptHistory';
import { FindingCard, type FindingDecision } from './FindingCard';
import { KeyboardHelpModal } from '../../components/ui/KeyboardHelpModal';
import { isTypingTarget } from '../../lib/keyboard';
import { checkLabel, reasonLabel, reviewStatusLabel, toolStatusLabel } from '../../lib/labels';
import { formatTimestamp } from '../../lib/format';
import { RECHECK_IN_PROGRESS_MESSAGE, useImageRecheckStatus } from '../../hooks/useImageRecheckStatus';
import { useDatasetName } from '../../hooks/useDatasetName';
import {
  DECISION_VARIANT,
  TOOL_VARIANT,
  colorFor,
  groupFindings,
  isReasonRequired,
  nextUnreviewedIndex,
  persistPaneWidth,
  readPaneWidth,
  resolveReviewKey,
  reviewProgress,
  rulesWithoutFindings,
  textColorFor,
} from './reviewWorkspace';
import type { components } from '../../api/generated/openapi';
import type { operations } from '../../api/generated/openapi';

type SampleAudit = components['schemas']['SampleAudit'];
type Finding = components['schemas']['Finding'];
type SampleReviewDecision = components['schemas']['SampleReviewDecision'];
type AuditQueueQuery = NonNullable<operations['listAuditSamples']['parameters']['query']>;
type AuditToolStatus = NonNullable<AuditQueueQuery['tool_status']>;
type AuditReviewStatus = NonNullable<AuditQueueQuery['review_status']>;

const ALL_FILTERS = { toolStatusFilter: 'all', reviewStatusFilter: 'all' } as const;

export const AuditReviewWorkspace: React.FC = () => {
  const { auditId } = useParams<{ auditId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const { toggleMask, toggleBoxes, togglePolygons, toggleLabels, resetView, zoom, setZoom, selectedInstanceId, setSelectedInstanceId } = useCanvasStore();
  const commentRef = useRef<HTMLTextAreaElement>(null);
  const datasetName = useDatasetName();
  const [searchParams] = useSearchParams();

  // `?sample=` lets the report and other screens deep-link to one sample.
  const [activeSampleId, setActiveSampleId] = useState<string | null>(() => searchParams.get('sample'));
  const [historicalAttemptId, setHistoricalAttemptId] = useState<string | null>(null);
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [hiddenObjectIds, setHiddenObjectIds] = useState<number[]>([]);
  const [reviewComment, setReviewComment] = useState<string>('');
  const [previewCorrection, setPreviewCorrection] = useState(false);
  const [savedCorrection, setSavedCorrection] = useState<{ sampleId: string; revision: number } | null>(null);
  const [isKeyboardHelpOpen, setIsKeyboardHelpOpen] = useState<boolean>(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  // Separate from reviewError: loading the other recheck's new attempt must not hide it.
  const [recheckNotice, setRecheckNotice] = useState<string | null>(null);
  const [recheckStarted, setRecheckStarted] = useState<string | null>(null);
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
  // Each attempt is its own audit or recheck; label them "Audit 014", "Audit 014 R01", ...
  const attemptNames = useDisplayNames({ auditIds: latestDetail.data?.attempts?.map((attempt) => attempt.audit_id) ?? [] });
  // Each earlier attempt's own result (shares the cache with historicalDetail) feeds the attempt trail.
  const attempts = latestDetail.data?.attempts ?? [];
  const olderAttemptDetails = useQueries({
    queries: attempts.slice(1).map((attempt) => ({
      queryKey: queryKeys.auditSample(attempt.audit_id, activeSampleId ?? ''),
      queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', {
        path: { audit_id: attempt.audit_id, sample_id: activeSampleId! },
      }),
      enabled: Boolean(activeSampleId),
    })),
  });
  // Corrections saved between attempts; only needed when the image's revision changed.
  const revisionChanged = new Set(attempts.map((attempt) => attempt.sample_revision)).size > 1;
  const revisionsQuery = useQuery({
    queryKey: ['revisions', latestDetail.data?.dataset_id ?? '', activeSampleId ?? ''],
    queryFn: () => apiGetAllPages('/datasets/{dataset_id}/samples/{sample_id}/revisions', {
      path: { dataset_id: latestDetail.data!.dataset_id, sample_id: activeSampleId! }, query: { limit: 100, offset: 0 },
    }),
    enabled: Boolean(revisionChanged && latestDetail.data?.dataset_id && activeSampleId),
  });
  const trail = attemptTrail(
    attempts,
    Object.fromEntries([
      ...attempts.slice(1).map((attempt, index) => [attempt.audit_id, olderAttemptDetails[index]?.data] as const),
      ...(latestDetail.data ? [[latestDetail.data.audit_id, latestDetail.data] as const] : []),
    ]),
    revisionsQuery.data?.items ?? [],
  );
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
    setPreviewCorrection(false);
    setSavedCorrection(null);
  }, [sampleDetail?.audit_id, activeSampleId]);

  const recheckMutation = useMutation({
    mutationFn: async (options?: { feedback?: boolean; propose?: boolean }) => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Set your reviewer name first (top right).'); }
      if (options?.feedback && !reviewComment.trim()) throw new Error('Enter feedback for this image first.');
      if (!auditId || !activeSampleId || !latestDetail.data) throw new Error('Load the image first.');
      return apiPost('/audits/{audit_id}/rechecks', {
        path: { audit_id: options?.feedback ? sampleDetail!.audit_id : auditId },
        body: {
          sample_ids: [activeSampleId],
          sample_revisions: { [activeSampleId]: savedCorrection?.sampleId === activeSampleId ? savedCorrection.revision : latestDetail.data.summary.sample_revision },
          reviewer_id: reviewerId.trim(),
          feedback: options?.feedback ? reviewComment.trim() : null,
          propose_correction: options?.propose ?? false,
        },
      });
    },
    onSuccess: (result) => {
      setHistoricalAttemptId(null);
      recheckStatus.track(result.job.job_id);
      setReviewError(null);
      setRecheckNotice(null);
      setRecheckStarted(`${auditLabel(result.audit.name)} started for this image.`);
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
      bbox: obj.bbox,
      ...('coordinate_system' in obj ? {
        coordinate_system: obj.coordinate_system,
        polygons: obj.polygons,
        bbox_origin: obj.bbox_origin,
        source_key: obj.source_key,
        source_id: obj.source_id,
      } : {}),
    }));
  }, [rawSample]);

  const proposal = useMemo(() => correctionPreview(sampleDetail?.measurements?.correction_proposal), [sampleDetail]);
  const proposalMatches = Boolean(
    proposal &&
    proposal.sample_id === activeSampleId &&
    proposal.dataset_id === sampleDetail?.dataset_id &&
    proposal.sample_revision === sampleDetail?.summary.sample_revision &&
    !historicalAttemptId,
  );

  // Proposal preview must never bleed across image, revision, or attempt changes
  useEffect(() => {
    setPreviewCorrection(false);
  }, [activeSampleId, sampleDetail?.summary.sample_revision, historicalAttemptId]);

  const hasMaskAsset = Boolean(
    sampleDetail?.assets?.mask?.url ||
    sampleDetail?.assets?.mask_preview?.url ||
    rawSample?.assets?.mask?.url ||
    rawSample?.assets?.mask_preview?.url,
  );
  const maskFailure = sampleDetail?.failures?.find((f) => f.reason === 'raw_mask_missing' || f.reason === 'raw_mask_unreadable');
  const annotationFormat = (rawSample?.annotation_format
    ?? (sampleDetail as unknown as { annotation_format?: string })?.annotation_format
    ?? (sampleDetail?.measurements?.correction_proposal as unknown as { annotation_format?: string })?.annotation_format) as 'penn_fudan' | 'coco' | 'labelme' | undefined;
  const isPennFudan = annotationFormat === 'penn_fudan';
  const isMaskMissing = !hasMaskAsset && (isPennFudan || Boolean(maskFailure));
  const undisplayableObjects = useMemo(() => {
    return canvasObjects
      .filter((obj) => obj.bbox == null && (!obj.polygons || obj.polygons.length === 0))
      .map((obj) => ({
        objectId: obj.object_id,
        displayNumber: canvasObjects.findIndex((o) => o.object_id === obj.object_id) + 1,
        label: obj.label,
      }));
  }, [canvasObjects]);
  const diagnosticsCount = (isMaskMissing ? 1 : 0) + undisplayableObjects.length + (rawSample?.issues?.length ?? 0);
  const applyProposal = useMutation({
    mutationFn: async () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Set your reviewer name first.'); }
      if (!proposalMatches || !proposal?.objects || !sampleDetail || historicalAttemptId) throw new Error('Load the current proposal first.');
      return apiPost('/datasets/{dataset_id}/samples/{sample_id}/revisions', {
        path: { dataset_id: proposal.dataset_id, sample_id: proposal.sample_id },
        body: { expected_revision: proposal.sample_revision, reviewer_id: reviewerId.trim(),
          reason: proposal.feedback.comment, objects: proposal.objects, mask_upload_id: null, source_audit_id: sampleDetail.audit_id },
      });
    },
    onSuccess: async (saved) => {
      setSavedCorrection({ sampleId: saved.sample_id, revision: saved.revision });
      setPreviewCorrection(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasetSample(saved.dataset_id, saved.sample_id) });
      try {
        const result = await apiPost('/audits/{audit_id}/rechecks', {
          path: { audit_id: auditId! }, body: { sample_ids: [saved.sample_id],
            sample_revisions: { [saved.sample_id]: saved.revision }, reviewer_id: reviewerId.trim(), feedback: null, propose_correction: false },
        });
        recheckStatus.track(result.job.job_id);
        setReviewError(null);
        setRecheckStarted(`Correction saved as r${saved.revision}. ${auditLabel(result.audit.name)} started for this image.`);
        void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
      } catch (error) {
        setReviewError(`Correction saved as r${saved.revision}, but recheck could not start. Use Re-check this image to retry. ${error instanceof Error ? error.message : ''}`);
      }
    },
    onError: (error) => setReviewError(error.message),
  });

  const findings: Finding[] = useMemo(() => sampleDetail?.findings ?? [], [sampleDetail]);
  const canReview = !!sampleDetail && !latestDetail.error && !historicalAttemptId
    && ['completed', 'failed'].includes(sampleDetail.summary.processing_status)
    && !recheckMutation.isPending && !applyProposal.isPending && !savedCorrection;
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
  const submittingImageChange = recheckMutation.isPending || applyProposal.isPending;

  const resizePane = (side: 'left' | 'right', delta: number) => {
    if (side === 'left') setLeftWidth((width) => persistPaneWidth('left', width + delta));
    else setRightWidth((width) => persistPaneWidth('right', width - delta));
  };

  const navigateQueue = useCallback(
    (direction: 'next' | 'prev') => {
      if (submittingImageChange || filteredQueue.length === 0) return;
      let nextIndex = direction === 'next' ? currentIndex + 1 : currentIndex - 1;
      if (nextIndex >= filteredQueue.length) nextIndex = 0;
      if (nextIndex < 0) nextIndex = filteredQueue.length - 1;
      const target = filteredQueue[nextIndex];
      if (target) setActiveSampleId(target.sample_id);
    },
    [filteredQueue, currentIndex, submittingImageChange],
  );

  const jumpToNextUnreviewed = useCallback(() => {
    if (submittingImageChange) return;
    const index = nextUnreviewedIndex(filteredQueue, currentIndex);
    const target = index >= 0 ? filteredQueue[index] : undefined;
    if (target) setActiveSampleId(target.sample_id);
    else setReviewError('Every sample in this view has a decision.');
  }, [filteredQueue, currentIndex, submittingImageChange]);

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
        case 'polygons': e.preventDefault(); togglePolygons(); break;
        case 'labels': e.preventDefault(); toggleLabels(); break;
        case 'help': e.preventDefault(); setIsKeyboardHelpOpen(true); break;
        default:
          if (e.key === '+' || e.key === '=') { e.preventDefault(); setZoom(zoom * 1.25); }
          if (e.key === '-' || e.key === '_') { e.preventDefault(); setZoom(zoom / 1.25); }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [decideFinding, decideSample, findings, selectedFindingId, navigateQueue, toggleMask, toggleBoxes, togglePolygons, toggleLabels, resetView, setZoom, zoom]);

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
            <div className="flex min-w-0 items-center gap-2 text-xs">
              {audit ? <AuditNameText name={audit.name} /> : <span className="text-text-muted">Loading audit…</span>}
              {audit && <TitleEditor kind="audit" id={audit.audit_id} name={audit.name} />}
            </div>
            <div className="text-2xs font-mono text-text-muted truncate">
              Dataset {datasetName(audit?.dataset_id)} · {audit ? rulebookLabel(audit.rulebook_name, audit.rulebook_version) : '…'}
            </div>
            {audit && <TechnicalId label="Audit ID" value={audit.audit_id} />}
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
      {recheckStarted && (
        <div role="status" className="review-notice">
          <RefreshCw size={14} /> <span>{recheckStarted}</span>
          <button onClick={() => setRecheckStarted(null)} aria-label="Dismiss">×</button>
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
                    disabled={submittingImageChange}
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
            annotationFormat={annotationFormat}
            assets={sampleDetail?.assets ?? rawSample?.assets}
            objects={previewCorrection && proposalMatches && proposal?.objects ? proposal.objects : canvasObjects}
            hiddenObjectIds={hiddenObjectIds}
            findings={findings}
            onSelectObject={selectObject}
            isCorrection={previewCorrection && proposalMatches}
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
            {previewCorrection && <p role="status" className="text-xs text-status-unsure">Previewing a proposed correction. Nothing has been saved.</p>}
            <section className="checklist-section" aria-label="Image rechecks">
              <Button size="sm" variant="secondary" leftIcon={<RefreshCw size={13} />}
                onClick={() => recheckMutation.mutate()} isLoading={recheckMutation.isPending}
                disabled={!latestDetail.data || attemptRunning || applyProposal.isPending || !recheckStatus.canRecheck || !!historicalAttemptId || sampleReviewMutation.isPending || findingReviewMutation.isPending || !audit || !['completed', 'failed', 'cancelled'].includes(audit.status)}>
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
            </section>
            {(trail.length > 1 || trail.some((step) => step.feedback)) && (
              <details className="checklist-section" open>
                <summary><h3>Attempt history <span>{trail.length}</span></h3></summary>
                <p className="checklist-empty">Each check of this image, oldest first: what led to it, the tool's result and the reviewer's decision. Select one to see its full result.</p>
                <AttemptTrail
                  steps={trail}
                  label={(id) => auditLabel(attemptNames.audit(id))}
                  selectedAuditId={historicalAttemptId}
                  onSelect={setHistoricalAttemptId}
                  disabled={sampleReviewMutation.isPending || findingReviewMutation.isPending || submittingImageChange}
                />
                {trail.some((step) => step.feedback) && (
                  <p className="checklist-empty mt-2">Feedback never changes the confirmed guidelines. Structural checks use the annotation data; available visual checks also consider the feedback.</p>
                )}
              </details>
            )}
            {historicalAttemptId && (
              <div role="status" className="attempt-viewing">
                <span>Previous attempt ({auditLabel(attemptNames.audit(historicalAttemptId))}) — read only. Its findings and decision are shown below.</span>
                <button type="button" onClick={() => setHistoricalAttemptId(null)}>Back to latest</button>
              </div>
            )}
            {isDetailLoading && <p className="text-xs text-text-muted"><Loader2 className="inline w-3 h-3 animate-spin" /> Loading findings…</p>}

            {sampleDetail?.failures && sampleDetail.failures.length > 0 && (
              <section className="checklist-section is-warning">
                <h3>Processing problems ({sampleDetail.failures.length})</h3>
                {sampleDetail.failures.map((failure) => (
                  <p key={failure.failure_id} className="text-2xs">{humanStage(failure.stage)}: {reasonLabel(failure.reason)} <span className="text-text-faint">— {failure.detail}</span></p>
                ))}
              </section>
            )}

            {sampleDetail?.measurements?.source_diagnostics != null && <details className="checklist-section">
              <summary>Source structure diagnostics</summary>
              <p className="text-2xs text-text-muted">Format and geometry observations. Final decisions follow the confirmed guidelines.</p>
              <pre className="text-2xs whitespace-pre-wrap break-all">{JSON.stringify(sampleDetail.measurements.source_diagnostics, null, 2)}</pre>
            </details>}

            {proposalMatches && proposal && <section className="checklist-section" aria-label="Proposed correction">
              <h3>Correction proposal</h3>
              <p className="text-xs">{proposal.reason}</p>
              <p className="text-2xs text-text-muted">Feedback: {proposal.feedback.comment}</p>
              {proposal.status === 'proposed' && proposal.objects && <div className="flex flex-wrap gap-2 mt-2">
                <Button size="sm" variant="secondary" onClick={() => setPreviewCorrection((value) => !value)}>{previewCorrection ? 'Show original' : 'Preview correction'}</Button>
                <Button size="sm" variant="primary" disabled={!previewCorrection || !canReview || attemptRunning || !recheckStatus.canRecheck || sampleReviewMutation.isPending || findingReviewMutation.isPending}
                  isLoading={applyProposal.isPending} onClick={() => applyProposal.mutate()}>Apply correction and recheck</Button>
              </div>}
              <p className="text-2xs text-text-muted mt-2">Preview first, then apply. Applying creates a new revision and checks this image again. Your final review is still required.</p>
            </section>}
            <details className="object-visibility" open>
              <summary>Objects on image · {canvasObjects.length}</summary>
              <div className="object-list-grid">{canvasObjects.map((object, idx) => {
                const hidden = hiddenObjectIds.includes(object.object_id);
                const displayNum = idx + 1;
                const objColor = colorFor(displayNum);
                const txtColor = textColorFor(displayNum);
                const isDerived = object.bbox_origin === 'derived_from_polygon';
                const hasBox = object.bbox != null;
                const hasPolygons = !!(object.polygons && object.polygons.length > 0);
                const noGeom = !hasBox && !hasPolygons;
                const sourceIdText = object.source_id != null ? String(object.source_id) : null;
                const sourceKeyText = object.source_key ?? null;
                const isSelected = selectedInstanceId === object.object_id;
                return (
                  <button
                    type="button"
                    key={object.object_id}
                    onClick={() => {
                      const nextId = isSelected ? null : object.object_id;
                      setSelectedInstanceId(nextId);
                      selectObject(nextId);
                    }}
                    title={`Click to select object on canvas. Display #${displayNum}, object_id=${object.object_id}${sourceIdText ? `, source=${sourceIdText}` : ''}${isDerived ? ' (box derived from polygon)' : ''}${noGeom ? ' (no displayable geometry)' : ''}`}
                    className={`${isSelected ? 'is-selected ' : ''}${hidden ? 'is-hidden' : ''}`.trim()}
                  >
                    <span className="object-list-swatch" style={{ background: objColor, color: txtColor }}>#{displayNum}</span>
                    <span className="object-list-details">
                      <span className="object-list-label">{object.label}{isDerived && <abbr title="Box derived from polygon vertices, not in source file">*</abbr>}{noGeom && <abbr title="No displayable geometry" className="object-list-warn">⚠</abbr>}</span>
                      <span className="object-list-meta">
                        {sourceIdText ? `id ${sourceIdText}` : `obj ${object.object_id}`}
                        {sourceKeyText && <> · {sourceKeyText}</>}
                      </span>
                    </span>
                    <span
                      role="button"
                      tabIndex={0}
                      className="object-list-eye"
                      onClick={(e) => {
                        e.stopPropagation();
                        setHiddenObjectIds((old) => hidden ? old.filter((id) => id !== object.object_id) : [...old, object.object_id]);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.stopPropagation();
                          e.preventDefault();
                          setHiddenObjectIds((old) => hidden ? old.filter((id) => id !== object.object_id) : [...old, object.object_id]);
                        }
                      }}
                      title={hidden ? 'Show on canvas' : 'Hide from canvas'}
                    >
                      {hidden ? <EyeOff size={11} /> : <Eye size={11} />}
                    </span>
                  </button>
                );
              })}</div>
            </details>

            {/* Annotation Diagnostics Inspector Panel */}
            <details className="annotation-diagnostics" open>
              <summary>Annotation diagnostics · {diagnosticsCount} note{diagnosticsCount === 1 ? '' : 's'}</summary>
              <div className="diagnostics-content">
                <div className="diagnostic-row">
                  <span className="diagnostic-label">Mask status:</span>
                  <span className="diagnostic-value">
                    {hasMaskAsset ? (
                      <span className="text-status-pass">✓ Mask asset loaded</span>
                    ) : isMaskMissing ? (
                      <span className="text-status-fail font-semibold">⚠ Referenced mask file is missing</span>
                    ) : (
                      <span className="text-text-muted">Optional mask never supplied</span>
                    )}
                  </span>
                </div>

                {undisplayableObjects.length > 0 ? (
                  <div className="diagnostic-row is-warning">
                    <span className="diagnostic-label">Unsupported geometry:</span>
                    <div className="diagnostic-list">
                      {undisplayableObjects.map((item) => (
                        <div key={item.objectId} className="diagnostic-item">
                          <span className="object-list-swatch" style={{ background: colorFor(item.displayNumber), color: textColorFor(item.displayNumber) }}>#{item.displayNumber}</span>
                          <span>{item.label}: No bounding box or polygon coordinates supplied in annotation data.</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="diagnostic-row">
                    <span className="diagnostic-label">Geometry:</span>
                    <span className="diagnostic-value text-status-pass">✓ All {canvasObjects.length} objects have displayable geometry</span>
                  </div>
                )}

                <div className="diagnostic-row">
                  <span className="diagnostic-label">Coordinate system:</span>
                  <span className="diagnostic-value">
                    {isPennFudan
                      ? 'Penn-Fudan: 1-based inclusive pixel bounds'
                      : `${rawSample?.annotation_format?.toUpperCase() ?? 'JSON'}: 0-based continuous coordinates`}
                  </span>
                </div>

                {rawSample?.issues && rawSample.issues.length > 0 && (
                  <div className="diagnostic-row is-warning">
                    <span className="diagnostic-label">Catalogue integrity issues:</span>
                    <div className="diagnostic-list">
                      {rawSample.issues.map((issue, idx) => (
                        <div key={idx} className="diagnostic-item">
                          <span className="font-mono text-2xs">{issue.field ?? 'general'}:</span>
                          <span>{issue.message}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
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
              <label htmlFor="review-comment" className="text-2xs font-mono uppercase tracking-wider text-text font-semibold">Feedback for this image</label>
              <span className="text-3xs font-mono text-text-faint">as {reviewerId || 'no reviewer set'}</span>
            </div>
            <textarea
              id="review-comment"
              ref={commentRef}
              rows={3}
              maxLength={4096}
              value={reviewComment}
              onChange={(e) => { setReviewComment(e.target.value); if (reviewError?.startsWith('Type a reason')) setReviewError(null); }}
              placeholder="Why? e.g. “Box misses the left arm” — required for overrides, rejects and corrections"
              className="review-comment"
              aria-describedby="review-comment-hint"
            />
            {reasonNeededHint && <p id="review-comment-hint" className="text-3xs text-text-faint">{reasonNeededHint}</p>}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" disabled={!canReview || !reviewComment.trim() || !recheckStatus.canRecheck || applyProposal.isPending}
                onClick={() => recheckMutation.mutate({ feedback: true })}>Recheck with feedback</Button>
              <Button size="sm" variant="secondary" disabled={!canReview || !reviewComment.trim() || !recheckStatus.canRecheck || applyProposal.isPending}
                onClick={() => recheckMutation.mutate({ feedback: true, propose: true })}>Suggest a correction</Button>
            </div>
            <p className="text-3xs text-text-faint">Feedback applies to this image only. It does not activate changes for future batches.</p>
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

function humanStage(stage?: string): string {
  if (!stage) return '';
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
