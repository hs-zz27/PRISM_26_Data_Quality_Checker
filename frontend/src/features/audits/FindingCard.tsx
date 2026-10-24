import React from 'react';
import { EnclosureEvidence, readEnclosureEvidence } from './EnclosureEvidence';
import { summarizeEvidence } from '../../lib/evidence';
import { History, Quote, UserRound } from 'lucide-react';
import { Badge, type BadgeVariant } from '../../components/ui/Badge';
import { checkLabel, describeRequirement, explainFinding, outcomeLabel, resolutionLabel } from '../../lib/labels';
import { formatTimestamp } from '../../lib/format';
import type { components } from '../../api/generated/openapi';

type Finding = components['schemas']['Finding'];
type FindingReviewRecord = components['schemas']['FindingReviewRecord'];
export type FindingDecision = 'accepted' | 'rejected' | 'unsure';

const OUTCOME_VARIANT: Record<string, BadgeVariant> = { pass: 'pass', fail: 'fail', unsure: 'unsure', unavailable: 'unavailable' };
const RESOLUTION_VARIANT: Record<string, BadgeVariant> = { pending: 'pending', accepted: 'accepted', rejected: 'rejected', unsure: 'unsure' };

interface FindingCardProps {
  finding: Finding;
  selected: boolean;
  onSelect: () => void;
  onDecide: (resolution: FindingDecision) => void;
  historyOpen: boolean;
  onToggleHistory: () => void;
  history?: FindingReviewRecord[];
  historyLoading?: boolean;
  disabled?: boolean;
}

/** One checklist row: rule, result, explanation, evidence and citation, plus the reviewer's decision. */
export const FindingCard: React.FC<FindingCardProps> = ({ finding: f, selected, onSelect, onDecide, historyOpen, onToggleHistory, history, historyLoading, disabled }) => {
  const resolution = f.review?.resolution ?? 'pending';
  const requirement = describeRequirement(f.check_name, f.parameters as Record<string, unknown>);
  const enclosure = f.check_name === 'pedestrian_box_enclosure'
    ? readEnclosureEvidence(f.evidence?.measurements?.enclosure) : null;
  const measurements = Object.fromEntries(Object.entries(f.evidence?.measurements ?? {})
    .filter(([key]) => !(enclosure && key === 'enclosure')));
  const evidenceRows = summarizeEvidence(f.check_name, measurements);
  const decisionButton = (value: FindingDecision, label: string, title: string, tone: string) => (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={resolution === value}
      title={title}
      onClick={(event) => { event.stopPropagation(); onDecide(value); }}
      className={`finding-decision ${tone}${resolution === value ? ' is-active' : ''}`}
    >
      {label}
    </button>
  );

  return (
    <article
      className={`finding-card${selected ? ' is-selected' : ''}`}
      onClick={onSelect}
      aria-label={`Rule ${f.rule_id}: ${outcomeLabel(f.outcome)}`}
    >
      <header className="finding-card-head">
        <Badge variant={OUTCOME_VARIANT[f.outcome] ?? 'unavailable'} size="sm">{outcomeLabel(f.outcome)}</Badge>
        <span className="finding-rule-id">{f.rule_id}</span>
        {f.object_id !== null && f.object_id !== undefined && <span className="finding-object">Object #{f.object_id}</span>}
        <span className="finding-spacer" />
        <Badge variant={RESOLUTION_VARIANT[resolution] ?? 'pending'} size="sm" icon={false}>{resolutionLabel(resolution)}</Badge>
      </header>

      <p className="finding-description">{f.description}</p>

      <dl className="finding-facts">
        <div><dt>Result</dt><dd>{explainFinding(f)}</dd></div>
        <div><dt>Check</dt><dd>{f.check_name ? checkLabel(f.check_name) : 'None — review manually'}</dd></div>
        {requirement && <div><dt>Requires</dt><dd>{requirement}</dd></div>}
      </dl>

      {enclosure && <EnclosureEvidence evidence={enclosure} />}

      {evidenceRows.length > 0 && (
        <div className="finding-evidence" aria-label="Measured evidence">
          <span className="finding-section-label">Evidence measured</span>
          {evidenceRows.map((row) => (
            <div key={row.label}><span>{row.label}</span><b>{row.value}</b></div>
          ))}
        </div>
      )}

      {Object.keys(measurements).length > 0 && (
        <details className="finding-evidence">
          <summary>Complete recorded evidence</summary>
          <pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(measurements, null, 2)}</pre>
        </details>
      )}

      {f.source_sentence ? (
        <blockquote className="finding-citation">
          <Quote size={12} aria-hidden="true" />
          <span><span className="finding-section-label">Guideline citation</span>“{f.source_sentence}”</span>
        </blockquote>
      ) : (
        <p className="finding-citation is-manual">
          <UserRound size={12} aria-hidden="true" />
          <span>{f.origin === 'reviewer' ? 'Reviewer-authored rule — no guideline sentence to cite.' : 'No citation recorded for this rule.'}</span>
        </p>
      )}

      {f.review?.comment && resolution !== 'pending' && (
        <p className="finding-last-comment">“{f.review.comment}” — {f.review.reviewer_id}</p>
      )}

      <footer className="finding-actions">
        {decisionButton('accepted', 'Agree', 'Agree with the tool result (A)', 'is-agree')}
        {decisionButton('rejected', 'Override', 'Tool is wrong — override it. A reason is required (R)', 'is-override')}
        {decisionButton('unsure', 'Unsure', 'Mark as unsure (U)', 'is-unsure')}
        <button type="button" className="finding-history-toggle" onClick={(event) => { event.stopPropagation(); onToggleHistory(); }} aria-expanded={historyOpen}>
          <History size={12} aria-hidden="true" /> History
        </button>
      </footer>

      {historyOpen && (
        <ol className="finding-history">
          {historyLoading && <li>Loading history…</li>}
          {!historyLoading && !history?.length && <li>No decisions recorded yet.</li>}
          {history?.map((entry) => (
            <li key={entry.revision}>
              <b>{resolutionLabel(entry.resolution)}</b> · {entry.reviewer_id} · {formatTimestamp(entry.updated_at)}
              {entry.comment && <span> — “{entry.comment}”</span>}
            </li>
          ))}
        </ol>
      )}
    </article>
  );
};
