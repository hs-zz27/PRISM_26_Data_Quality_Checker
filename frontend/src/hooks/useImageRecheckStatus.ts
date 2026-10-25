/**
 * useImageRecheckStatus — may this image be rechecked right now?
 * ─────────────────────────────────────────────────────────────────────────
 * Mirrors the backend RECHECK_ALREADY_RUNNING guard: an image is blocked while
 * any attempt in its audit family has a queued, running or cancelling job.
 * Attempts come from the image's latest detail (include_rechecks=true), so the
 * answer survives a page refresh. Each attempt's job is polled until finished;
 * finished jobs are read once. Status is per audit family and image, so other
 * images stay independently recheckable.
 * ─────────────────────────────────────────────────────────────────────────
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet } from '../api/client';
import { queryKeys } from '../api/queryClient';
import { useJobStore } from '../store/jobStore';
import { isJobActive, isJobTerminal, type Job } from './useJobPolling';

export type RecheckAvailability = 'loading' | 'error' | 'active' | 'idle';

export const RECHECK_IN_PROGRESS_MESSAGE = 'This image already has a recheck in progress.';

const ACTIVE_POLL_MS = 2000;

/** Same cache entry the review workspace uses for the image's newest attempt. */
export const latestSampleDetailKey = (auditId: string, sampleId: string) =>
  [...queryKeys.auditSample(auditId, sampleId), 'latest'] as const;

export function useImageRecheckStatus(auditId: string | null | undefined, sampleId: string | null | undefined) {
  const queryClient = useQueryClient();
  const enabled = Boolean(auditId && sampleId);
  const imageKey = `${auditId ?? ''}\u0000${sampleId ?? ''}`;
  // Jobs this page just submitted, until the image's detail lists them as attempts.
  const [submitted, setSubmitted] = useState<Record<string, string[]>>({});

  const detail = useQuery({
    queryKey: latestSampleDetailKey(auditId ?? '', sampleId ?? ''),
    queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', {
      path: { audit_id: auditId!, sample_id: sampleId! },
      query: { include_rechecks: true },
    }),
    enabled,
  });

  const jobIds = useMemo(() => {
    const ids = (detail.data?.attempts ?? []).map((attempt) => attempt.job_id);
    for (const id of submitted[imageKey] ?? []) if (!ids.includes(id)) ids.push(id);
    return ids;
  }, [detail.data, submitted, imageKey]);

  const jobs = useQueries({
    queries: jobIds.map((jobId) => ({
      queryKey: queryKeys.job(jobId),
      queryFn: () => apiGet('/jobs/{job_id}', { path: { job_id: jobId } }),
      enabled,
      // A finished job never changes again.
      staleTime: (query: { state: { data?: Job } }) => (query.state.data && isJobTerminal(query.state.data.status) ? Infinity : 0),
      refetchInterval: (query: { state: { data?: Job } }) => (query.state.data && isJobTerminal(query.state.data.status) ? false : ACTIVE_POLL_MS),
    })),
  });

  const activeJob = jobs.find((job) => job.data && isJobActive(job.data.status))?.data;
  const jobsLoading = jobs.some((job) => job.isPending);
  const jobError = jobs.find((job) => !job.data && job.error)?.error ?? null;

  let state: RecheckAvailability;
  if (!enabled || detail.isPending) state = 'loading';
  else if (detail.error && !detail.data) state = 'error';
  else if (activeJob) state = 'active';
  else if (jobError) state = 'error';
  else if (jobsLoading) state = 'loading';
  else state = 'idle';

  // Show active rechecks in the job tray and counter, even after a refresh.
  const addTracked = useJobStore((s) => s.add);
  const setTrackedStatus = useJobStore((s) => s.setStatus);
  useEffect(() => {
    if (activeJob) { addTracked(activeJob.job_id); setTrackedStatus(activeJob.job_id, activeJob.status); }
  }, [activeJob, addTracked, setTrackedStatus]);

  // When a job this image was waiting on finishes, reload its results.
  const watching = useRef<Set<string>>(new Set());
  useEffect(() => {
    let finished = false;
    for (const job of jobs) {
      if (!job.data) continue;
      if (isJobActive(job.data.status)) watching.current.add(job.data.job_id);
      else if (watching.current.delete(job.data.job_id)) finished = true;
    }
    if (finished && auditId && sampleId) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.auditSample(auditId, sampleId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.auditSamples(auditId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
    }
  }, [jobs, auditId, sampleId, queryClient]);

  /** Start watching a recheck this page just submitted. */
  const track = useCallback((jobId: string) => {
    setSubmitted((old) => ({ ...old, [imageKey]: [...(old[imageKey] ?? []), jobId] }));
    void queryClient.invalidateQueries({ queryKey: latestSampleDetailKey(auditId ?? '', sampleId ?? '') });
  }, [imageKey, auditId, sampleId, queryClient]);

  /** Reload the image's attempts and every attempt's job (after a 409 or an error). */
  const refresh = useCallback(() => {
    void detail.refetch();
    for (const job of jobs) if (!job.data || !isJobTerminal(job.data.status)) void job.refetch();
  }, [detail, jobs]);

  return {
    state,
    canRecheck: state === 'idle',
    activeJob,
    error: state === 'error' ? (detail.error ?? jobError) : null,
    track,
    refresh,
  };
}
