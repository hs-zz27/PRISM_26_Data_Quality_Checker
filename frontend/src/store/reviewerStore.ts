/**
 * Reviewer Store
 * ─────────────────────────────────────────────────────────────────────────
 * Manages the active reviewer_id.
 * Mandatory and non-blank on every mutating call across PRISM.
 * Persisted in localStorage.
 * ─────────────────────────────────────────────────────────────────────────
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface ReviewerState {
  reviewerId: string;
  isPromptOpen: boolean;
  setReviewerId: (id: string) => void;
  openPrompt: () => void;
  closePrompt: () => void;
  /** Returns current non-empty reviewerId or null */
  getActiveId: () => string | null;
}

export const useReviewerStore = create<ReviewerState>()(
  persist(
    (set, get) => ({
      reviewerId: 'rev_alice', // sensible initial default per PRISM examples
      isPromptOpen: false,
      setReviewerId: (id: string) => set({ reviewerId: id.trim() }),
      openPrompt: () => set({ isPromptOpen: true }),
      closePrompt: () => set({ isPromptOpen: false }),
      getActiveId: () => {
        const id = get().reviewerId.trim();
        return id.length > 0 ? id : null;
      },
    }),
    {
      name: 'prism_reviewer_store',
    },
  ),
);
