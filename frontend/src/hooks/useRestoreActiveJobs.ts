/**
 * useRestoreActiveJobs — rebuilds the job tray after a page load.
 * ─────────────────────────────────────────────────────────────────────────
 * The backend has no "list jobs" route, so active jobs are found through the
 * lists that carry a job_id: audits (including rechecks), exports, dataset
 * imports and feedback evaluations. Rule extractions have no list; they are
 * kept by the persisted job store instead. A list that fails to load is
 * skipped so the others still restore.
 * ─────────────────────────────────────────────────────────────────────────
 */

import { useEffect } from 'react';
import { apiGetAllPages } from '../api/client';
import { useJobStore } from '../store/jobStore';
import { isJobActive, type JobStatus } from './useJobPolling';

type Found = { jobId: string; status?: JobStatus };

export async function findActiveJobs(): Promise<Found[]> {
  const page = { query: { limit: 100, offset: 0 } };
  const [audits, exports, datasets, evaluations] = await Promise.allSettled([
    apiGetAllPages('/audits', page),
    apiGetAllPages('/exports', page),
    apiGetAllPages('/datasets', page),
    apiGetAllPages('/feedback/evaluations', page),
  ]);
  const found: Found[] = [];
  const withStatus = (items: Array<{ job_id: string; status: JobStatus }>) => {
    for (const item of items) if (isJobActive(item.status)) found.push({ jobId: item.job_id, status: item.status });
  };
  if (audits.status === 'fulfilled') withStatus(audits.value.items);
  if (exports.status === 'fulfilled') withStatus(exports.value.items);
  if (evaluations.status === 'fulfilled') withStatus(evaluations.value.items);
  // A dataset only says "importing"; the job's exact status comes from its first poll.
  if (datasets.status === 'fulfilled') {
    for (const dataset of datasets.value.items) if (dataset.status === 'importing') found.push({ jobId: dataset.import_job_id });
  }
  return found;
}

export function useRestoreActiveJobs() {
  useEffect(() => {
    let cancelled = false;
    void findActiveJobs().then((found) => {
      if (cancelled) return;
      const { add, setStatus } = useJobStore.getState();
      for (const { jobId, status } of found) {
        add(jobId);
        if (status) setStatus(jobId, status);
      }
    });
    return () => { cancelled = true; };
  }, []);
}
