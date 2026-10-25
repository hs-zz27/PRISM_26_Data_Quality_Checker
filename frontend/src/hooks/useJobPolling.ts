/**
 * useJobPolling — Universal lifecycle hook for asynchronous jobs.
 * ─────────────────────────────────────────────────────────────────────────
 * Handles all 6 JobStatus states:
 *   - In-flight: queued, running, cancelling (polls with backoff)
 *   - Terminal:  completed, failed, cancelled (stops polling, invalidates queries)
 *
 * Invalidation on completion:
 *   - rule_extraction: invalidates queryKeys.rulebooks()
 *   - dataset_import:  invalidates queryKeys.datasets()
 *   - audit:           invalidates queryKeys.audits() and specific audit queries
 *   - recheck:         invalidates audits and audit samples
 *   - export:          invalidates queryKeys.exports()
 *   - feedback_evaluation: invalidates feedback evaluations
 * ─────────────────────────────────────────────────────────────────────────
 */

import { useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiPost } from '../api/client';
import { queryKeys } from '../api/queryClient';
import type { components } from '../api/generated/openapi';
import { useJobStore } from '../store/jobStore';

export type Job = components['schemas']['Job'];
export type JobStatus = components['schemas']['JobStatus'];
export type JobKind = components['schemas']['JobKind'];

export const ACTIVE_JOB_STATUSES: JobStatus[] = ['queued', 'running', 'cancelling'];
export const TERMINAL_JOB_STATUSES: JobStatus[] = ['completed', 'failed', 'cancelled'];

export function isJobActive(status: JobStatus): boolean {
  return ACTIVE_JOB_STATUSES.includes(status);
}

export function isJobTerminal(status: JobStatus): boolean {
  return TERMINAL_JOB_STATUSES.includes(status);
}

export function getJobPollDelay(intervalMs: number, attempt: number): number {
  return Math.min(intervalMs * (1.5 ** Math.min(attempt + 1, 5)), 15_000);
}

export interface UseJobPollingOptions {
  enabled?: boolean;
  intervalMs?: number;
  onSuccess?: (job: Job) => void;
  onError?: (job: Job) => void;
  onCancelled?: (job: Job) => void;
}

export function useJobPolling(jobId: string | null | undefined, options: UseJobPollingOptions = {}) {
  const {
    enabled = true,
    intervalMs = 1500,
    onSuccess,
    onError,
    onCancelled,
  } = options;

  const queryClient = useQueryClient();
  const addTrackedJob = useJobStore((state) => state.add);
  useEffect(() => { if (jobId) addTrackedJob(jobId); }, [jobId, addTrackedJob]);
  const lastTerminalRef = useRef<{ jobId: string | null; status: JobStatus | null }>({ jobId: null, status: null });
  const pollCountRef = useRef(0);
  useEffect(() => {
    lastTerminalRef.current = { jobId: jobId ?? null, status: null };
    pollCountRef.current = 0;
  }, [jobId]);

  const query = useQuery({
    queryKey: jobId ? queryKeys.job(jobId) : ['jobs', 'null'],
    queryFn: () => apiGet('/jobs/{job_id}', { path: { job_id: jobId! } }),
    enabled: Boolean(jobId && enabled),
    refetchInterval: (queryState) => {
      const data = queryState.state.data;
      if (!data) return intervalMs;
      if (isJobTerminal(data.status)) {
        return false;
      }
      pollCountRef.current += 1;
      return getJobPollDelay(intervalMs, pollCountRef.current);
    },
  });

  const job = query.data;
  const setTrackedStatus = useJobStore((state) => state.setStatus);
  useEffect(() => { if (job) setTrackedStatus(job.job_id, job.status); }, [job, setTrackedStatus]);

  // Invalidate queries & fire callbacks when a job transitions to a terminal state
  useEffect(() => {
    if (!job || !isJobTerminal(job.status)) return;
    if (lastTerminalRef.current.jobId === jobId && lastTerminalRef.current.status === job.status) return;

    lastTerminalRef.current = { jobId: jobId ?? null, status: job.status };

    // Cache invalidation based on job kind and result
    if (job.kind === 'rule_extraction') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.rulebooks() });
    } else if (job.kind === 'dataset_import') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
    } else if (job.kind === 'audit') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
      if (job.result?.id) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.audit(job.result.id) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.auditSamples(job.result.id) });
      }
    } else if (job.kind === 'recheck') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
      if (job.result?.id) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.audit(job.result.id) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.auditSamples(job.result.id) });
      }
    } else if (job.kind === 'export') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.exports() });
    } else if (job.kind === 'feedback_evaluation') {
      void queryClient.invalidateQueries({ queryKey: queryKeys.feedbackEvaluations() });
    }

    if (job.status === 'completed') {
      onSuccess?.(job);
    } else if (job.status === 'failed') {
      onError?.(job);
    } else if (job.status === 'cancelled') {
      onCancelled?.(job);
    }
  }, [job, jobId, queryClient, onSuccess, onError, onCancelled]);

  // Cancel mutation
  const cancelMutation = useMutation<Job, Error>({
    mutationFn: () => {
      if (!jobId) throw new Error('Cannot cancel job without jobId');
      return apiPost('/jobs/{job_id}/cancel', { path: { job_id: jobId } });
    },
    onSuccess: (updatedJob) => {
      if (jobId) {
        queryClient.setQueryData(queryKeys.job(jobId), updatedJob);
      }
    },
  });

  return {
    job,
    status: job?.status,
    progress: job?.progress,
    result: job?.result,
    error: job?.error ?? query.error,
    isLoading: query.isLoading,
    isActive: job ? isJobActive(job.status) : false,
    isCompleted: job?.status === 'completed',
    isFailed: job?.status === 'failed',
    isCancelled: job?.status === 'cancelled',
    isCancelling: job?.status === 'cancelling',
    cancel: cancelMutation.mutate,
    isCancellingRequest: cancelMutation.isPending,
    refetch: query.refetch,
  };
}
