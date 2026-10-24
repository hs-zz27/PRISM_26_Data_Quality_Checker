import React from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Keyboard, X } from 'lucide-react';
import { SHORTCUTS, type ShortcutCategory } from '../../lib/keyboard';

interface KeyboardHelpModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const CATEGORY_TITLES: Record<ShortcutCategory, string> = {
  review: 'Review Verdicts',
  navigation: 'Queue Navigation',
  canvas: 'Canvas & Layers',
  global: 'Global',
};

export const KeyboardHelpModal: React.FC<KeyboardHelpModalProps> = ({ open, onOpenChange }) => {
  const categories: ShortcutCategory[] = ['review', 'navigation', 'canvas', 'global'];

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-base-pure/80 backdrop-blur-sm z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg bg-surface border border-border p-6 rounded-lg shadow-xl z-50 animate-in zoom-in-95">
          <div className="flex items-center justify-between pb-3 border-b border-border mb-4">
            <div className="flex items-center gap-2 text-text font-medium text-sm">
              <Keyboard className="w-4 h-4 text-accent" />
              <span>Keyboard Ergonomics</span>
            </div>
            <button
              onClick={() => onOpenChange(false)}
              className="text-text-muted hover:text-text p-1 rounded"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-1">
            {categories.map((cat) => {
              const items = SHORTCUTS.filter((s) => s.category === cat);
              if (items.length === 0) return null;
              return (
                <div key={cat}>
                  <div className="text-2xs font-mono uppercase tracking-wider text-text-muted mb-2">
                    {CATEGORY_TITLES[cat]}
                  </div>
                  <div className="space-y-1.5">
                    {items.map((s) => (
                      <div
                        key={s.key}
                        className="flex items-center justify-between py-1 px-2 rounded bg-surface-high/60 border border-border/40 text-xs"
                      >
                        <span className="text-text">{s.description}</span>
                        <kbd className="px-2 py-0.5 rounded bg-base border border-border-muted font-mono font-semibold text-accent text-2xs shadow-inner">
                          {s.display}
                        </kbd>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-4 pt-3 border-t border-border text-2xs text-text-faint text-center">
            Shortcuts are automatically disabled while typing in input fields.
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
