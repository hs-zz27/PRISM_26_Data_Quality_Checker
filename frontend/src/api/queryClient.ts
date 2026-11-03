/**
 * TanStack Query client configuration.
 * ─────────────────────────────────────────────────────────────────────────
 * Configured for a dense real-time review workstation:
 *   - staleTime: 30s for most data (rulebooks, samples don't change often)
 *   - gcTime: 5 min (keep data in memory while switching panels)
 *   - retry: 1 (network issues are transient; don't hammer the backend)
 *   - No retries on 4xx (user errors, version conflicts)
 * ─────────────────────────────────────────────────────────────────────────
 */

import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './client';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,        // 30s — data is relatively stable
      gcTime: 5 * 60_000,       // 5min — keep while user is navigating
      retry: (failureCount, error) => {
        // Never retry on 4xx (validation errors, conflicts, not found)
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        // One retry for 5xx / network errors
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,  // Workstation app — don't surprise the reviewer
    },
    mutations: {
      retry: false,  // Mutations (decisions, saves) must never auto-retry
    },
  },
});

/** Query key factories — centralized so invalidation is always consistent. */
export const queryKeys = {
  // Rulebooks
  rulebooks: () => ['rulebooks'] as const,
  rulebook: (id: string, version?: number) =>
    ['rulebooks', id, version ?? 'latest'] as const,
  rulebookVersions: (id: string) => ['rulebooks', id, 'versions'] as const,
  rulebookReviews: (id: string) => ['rulebooks', id, 'reviews'] as const,

  // Display names for IDs held by other records; title edits invalidate the prefix.
  displayNames: () => ['display-names'] as const,
  displayNameLookup: (auditIds: readonly string[], rulebookIds: readonly string[]) =>
    ['display-names', auditIds, rulebookIds] as const,

  // Datasets
  datasets: () => ['datasets'] as const,
  dataset: (id: string) => ['datasets', id] as const,
  datasetSamples: (id: string, params?: Record<string, unknown>) =>
    ['datasets', id, 'samples', params] as const,
  datasetSample: (datasetId: string, sampleId: string, revision?: number) =>
    ['datasets', datasetId, 'samples', sampleId, revision ?? 'head'] as const,

  // Jobs
  job: (id: string) => ['jobs', id] as const,

  // Audits
  audits: () => ['audits'] as const,
  auditWorkspaces: () => ['audits', 'workspaces'] as const,
  audit: (id: string) => ['audits', id] as const,
  auditSamples: (id: string, params?: Record<string, unknown>) =>
    params === undefined ? ['audits', id, 'samples'] as const : ['audits', id, 'samples', params] as const,
  auditSample: (auditId: string, sampleId: string) =>
    ['audits', auditId, 'samples', sampleId] as const,

  // Reviews
  findingReviews: (findingId: string) => ['findings', findingId, 'reviews'] as const,
  sampleReviews: (auditId: string, sampleId: string, revision: number) =>
    ['audits', auditId, 'samples', sampleId, 'reviews', revision] as const,

  // System
  health: () => ['system', 'health'] as const,
  capabilities: () => ['system', 'capabilities'] as const,
  systemStatus: () => ['system', 'status'] as const,

  // Exports
  exports: () => ['exports'] as const,
  export: (id: string) => ['exports', id] as const,

  // Feedback
  feedbackExamples: () => ['feedback', 'examples'] as const,
  feedbackPolicies: () => ['feedback', 'policies'] as const,
  feedbackPolicy: (id: string) => ['feedback', 'policies', id] as const,
  feedbackEvaluations: () => ['feedback', 'evaluations'] as const,
  feedbackEvaluation: (id: string) => ['feedback', 'evaluations', id] as const,
  groundTruth: (id: string) => ['feedback', 'ground-truth', id] as const,
} as const;
