import React, { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { UserCheck, AlertTriangle } from 'lucide-react';
import { useReviewerStore } from '../../store/reviewerStore';
import { Button } from './Button';

export const ReviewerPromptModal: React.FC = () => {
  const { reviewerId, setReviewerId, isPromptOpen, closePrompt } = useReviewerStore();
  const [tempId, setTempId] = useState(reviewerId);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = tempId.trim();
    if (!trimmed) {
      setError('Reviewer ID cannot be blank. Every decision is attributed.');
      return;
    }
    setReviewerId(trimmed);
    setError(null);
    closePrompt();
  };

  return (
    <Dialog.Root open={isPromptOpen} onOpenChange={(open) => !open && closePrompt()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-base-pure/80 backdrop-blur-sm z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-md bg-surface border border-border p-6 rounded-lg shadow-xl z-50 animate-in zoom-in-95">
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 bg-accent/10 rounded-md text-accent border border-accent/20">
              <UserCheck className="w-5 h-5" />
            </div>
            <div>
              <Dialog.Title className="text-base font-semibold text-text">
                Reviewer Attribution
              </Dialog.Title>
              <Dialog.Description className="text-xs text-text-muted mt-0.5">
                Every mutation, rule confirmation, and audit review requires a valid reviewer ID.
              </Dialog.Description>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="reviewer-id-input" className="block text-xs font-mono text-text-muted mb-1.5 uppercase">
                Reviewer ID
              </label>
              <input
                id="reviewer-id-input"
                type="text"
                value={tempId}
                onChange={(e) => {
                  setTempId(e.target.value);
                  if (error) setError(null);
                }}
                placeholder="e.g. rev_alice or your corporate ID"
                autoFocus
                className="w-full bg-surface-high border border-border rounded px-3 py-2 text-sm font-mono text-text placeholder:text-text-faint focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent"
              />
              {error && (
                <div className="flex items-center gap-1.5 text-xs text-status-fail mt-2">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={closePrompt}>
                Cancel
              </Button>
              <Button type="submit" variant="primary">
                Save Reviewer ID
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
