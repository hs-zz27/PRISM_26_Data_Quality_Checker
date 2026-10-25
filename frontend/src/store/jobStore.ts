import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { components } from '../api/generated/openapi';

type JobStatus = components['schemas']['JobStatus'];
// Kept local: useJobPolling imports this store, so importing its helpers here would be circular.
const FINISHED: readonly JobStatus[] = ['completed', 'failed', 'cancelled'];

interface JobsState {
  ids: string[];
  /** Last status seen for each tracked job; missing until its first poll. */
  statuses: Record<string, JobStatus>;
  add: (id: string) => void;
  remove: (id: string) => void;
  setStatus: (id: string, status: JobStatus) => void;
}

/** Jobs shown in the tray. Unfinished ones are persisted so a page refresh keeps them. */
export const useJobStore = create<JobsState>()(
  persist(
    (set) => ({
      ids: [],
      statuses: {},
      add: (id) => set((s) => ({ ids: s.ids.includes(id) ? s.ids : [...s.ids, id] })),
      remove: (id) => set((s) => ({
        ids: s.ids.filter((item) => item !== id),
        statuses: Object.fromEntries(Object.entries(s.statuses).filter(([key]) => key !== id)),
      })),
      setStatus: (id, status) => set((s) => (s.statuses[id] === status ? s : { statuses: { ...s.statuses, [id]: status } })),
    }),
    {
      name: 'prism_job_store',
      partialize: (s) => {
        const ids = s.ids.filter((id) => !FINISHED.includes(s.statuses[id] as JobStatus));
        return { ids, statuses: Object.fromEntries(ids.filter((id) => s.statuses[id]).map((id) => [id, s.statuses[id]])) };
      },
    },
  ),
);

/** Number of tracked jobs currently queued, running or cancelling. */
export const selectActiveJobCount = (s: JobsState) =>
  s.ids.filter((id) => s.statuses[id] !== undefined && !FINISHED.includes(s.statuses[id])).length;
