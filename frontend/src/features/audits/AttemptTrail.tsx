import React from 'react';
import { PenLine } from 'lucide-react';
import { Badge } from '../../components/ui/Badge';
import { formatTimestamp } from '../../lib/format';
import { jobStatusLabel, reviewStatusLabel, toolStatusLabel } from '../../lib/labels';
import { trailCause, type TrailStep } from './attemptHistory';
import { DECISION_VARIANT, TOOL_VARIANT } from './reviewWorkspace';

interface AttemptTrailProps {
  steps: TrailStep[];
  /** Readable name of an attempt, e.g. "Audit 014 R01". */
  label: (auditId: string) => string;
  /** The attempt on screen; null means the latest. */
  selectedAuditId: string | null;
  onSelect: (auditId: string | null) => void;
  disabled?: boolean;
}

/** An image's attempts, oldest first: what led to each one, its tool result and the reviewer's decision. */
export const AttemptTrail: React.FC<AttemptTrailProps> = ({ steps, label, selectedAuditId, onSelect, disabled }) => (
  <ol className="attempt-trail" aria-label="Attempt history">
    {steps.map((step) => {
      const selected = selectedAuditId ? step.auditId === selectedAuditId : step.isLatest;
      const counts = step.findingCounts;
      const countText = counts ? [
        counts.fail ? `${counts.fail} fail` : null,
        counts.unsure ? `${counts.unsure} unsure` : null,
        counts.unavailable ? `${counts.unavailable} could not check` : null,
      ].filter(Boolean).join(' · ') : '';
      return (
        <li key={step.auditId} className={`attempt-step${selected ? ' is-selected' : ''}`}>
          <button type="button" aria-pressed={selected} disabled={disabled}
            onClick={() => onSelect(step.isLatest ? null : step.auditId)}>
            <span className="attempt-step-head">
              <span className="attempt-step-name">{label(step.auditId)}</span>
              {step.isLatest && <span className="attempt-step-tag">Latest</span>}
              {selected && <span className="attempt-step-tag is-viewing">Viewing</span>}
            </span>
            <span className="attempt-step-meta">{formatTimestamp(step.createdAt)} · revision {step.revision}</span>
            <span className="attempt-step-cause">{trailCause(step)}</span>

            {step.corrections.map((correction) => (
              <span key={correction.revision} className="attempt-step-note">
                <PenLine size={11} aria-hidden="true" />
                <span>Correction saved as revision {correction.revision} by <b>{correction.by}</b>{correction.reason ? <>: “{correction.reason}”</> : null}</span>
              </span>
            ))}

            {step.feedback && (
              <span className="attempt-step-quote">
                <span className="attempt-step-quote-text">“{step.feedback.comment}”</span>
                <span className="attempt-step-quote-by">
                  — {step.feedback.by}{step.feedback.objectKeys.length
                    ? ` · about ${step.feedback.objectKeys.length === 1 ? 'object' : 'objects'} ${step.feedback.objectKeys.join(', ')}` : ''}
                </span>
              </span>
            )}

            <span className="attempt-step-verdicts">
              <span className="attempt-step-verdict-label">Tool</span>
              <span className="attempt-step-verdict-value">
                {step.processingStatus !== 'completed' && step.processingStatus !== 'failed'
                  ? <Badge variant={step.processingStatus === 'cancelled' ? 'unavailable' : 'pending'} size="sm">{jobStatusLabel(step.processingStatus)}</Badge>
                  : step.toolStatus
                    ? <Badge variant={TOOL_VARIANT[step.toolStatus] ?? 'unavailable'} size="sm">{toolStatusLabel(step.toolStatus)}</Badge>
                    : <span className="attempt-step-loading">Loading…</span>}
                {countText && <span className="attempt-step-counts">{countText}</span>}
              </span>
              <span className="attempt-step-verdict-label">Reviewer</span>
              <span className="attempt-step-verdict-value">
                {step.review
                  ? <Badge variant={DECISION_VARIANT[step.review.decision] ?? 'pending'} size="sm">{reviewStatusLabel(step.review.decision)}</Badge>
                  : <span className="attempt-step-loading">Loading…</span>}
              </span>
            </span>
            {step.review?.by && (
              <span className="attempt-step-note">
                <span>Decided by <b>{step.review.by}</b>{step.review.at ? ` · ${formatTimestamp(step.review.at)}` : ''}{step.review.comment ? <>: “{step.review.comment}”</> : null}</span>
              </span>
            )}
          </button>
        </li>
      );
    })}
  </ol>
);
