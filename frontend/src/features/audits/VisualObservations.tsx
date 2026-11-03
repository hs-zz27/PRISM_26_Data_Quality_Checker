import type { components } from '../../api/generated/openapi';
import { FeedbackComparison } from './FeedbackComparison';

type Observation = components['schemas']['VisualObservation'];
type Scope = components['schemas']['TargetScope'] & {
  results: NonNullable<components['schemas']['TargetScope']['results']>;
  ineligible: NonNullable<components['schemas']['TargetScope']['ineligible']>;
  notes: string[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Older or malformed evidence keeps the existing complete-evidence fallback. */
function readObservations(value: unknown): Observation[] | null {
  if (!isRecord(value) || value.model_verified !== true || !isRecord(value.visual_assessment)) return null;
  const rows = value.visual_assessment.observations;
  if (!Array.isArray(rows) || rows.length === 0 || rows.length > 64) return null;
  const keys = new Set<string>();
  for (const row of rows) {
    if (!isRecord(row) || typeof row.object_key !== 'string' || keys.has(row.object_key)
      || typeof row.reason !== 'string' || !row.reason.trim()
      || typeof row.outcome !== 'string' || !['pass', 'fail', 'unsure', 'unavailable', 'not_applicable'].includes(row.outcome)
      || !Array.isArray(row.geometry) || !row.geometry.every((kind) => ['box', 'polygon', 'label', 'mask'].includes(kind))) return null;
    if (row.region !== null && (!Array.isArray(row.region) || row.region.length !== 4
      || !row.region.every((coordinate) => typeof coordinate === 'number' && Number.isFinite(coordinate)))) return null;
    keys.add(row.object_key);
  }
  return rows as Observation[];
}

const OUTCOMES: Record<Observation['outcome'], string> = {
  pass: 'Pass', fail: 'Fail', unsure: 'Unsure', unavailable: 'Unavailable', not_applicable: 'Not applicable',
};

function readScope(proposal: unknown): Scope | null {
  if (!isRecord(proposal) || !isRecord(proposal.scope)) return null;
  const scope = proposal.scope;
  if (scope.version !== 'target-scope-v1' || !Array.isArray(scope.results)
    || !Array.isArray(scope.ineligible) || !Array.isArray(scope.notes)
    || !scope.notes.every((note) => typeof note === 'string')) return null;
  if (scope.outcome_source === 'derived_from_target_results') {
    if (proposal.model_verified !== true) return null;
  } else if (scope.outcome_source !== 'no_model_call' || !isRecord(scope.unavailable)
    || typeof scope.unavailable.detail !== 'string' || scope.results.length !== 0) return null;
  if (scope.unavailable != null && (!isRecord(scope.unavailable) || typeof scope.unavailable.detail !== 'string')) return null;
  const keys = new Set<string>();
  for (const row of scope.results) {
    if (!isRecord(row) || typeof row.object_key !== 'string' || keys.has(row.object_key)
      || typeof row.outcome !== 'string' || !['pass', 'fail', 'unsure', 'unavailable'].includes(row.outcome)
      || (row.geometry != null && !['box', 'polygon'].includes(String(row.geometry)))
      || (row.reason != null && typeof row.reason !== 'string')
      || !Array.isArray(row.flags) || !row.flags.every((flag) => typeof flag === 'string')) return null;
    keys.add(row.object_key);
  }
  for (const row of scope.ineligible) {
    if (!isRecord(row) || typeof row.object_key !== 'string' || keys.has(row.object_key)
      || typeof row.detail !== 'string') return null;
    keys.add(row.object_key);
  }
  return scope as unknown as Scope;
}

function ScopedObservations({ scope }: { scope: Scope }) {
  return (
    <section className="finding-evidence" aria-label="Per-object visual observations">
      <span className="finding-section-label">Per-object visual observations</span>
      {scope.unavailable && <p className="text-xs">{scope.unavailable.detail}</p>}
      <ul className="space-y-2 text-xs">
        {scope.results.map((row) => (
          <li key={row.object_key}>
            <b>{row.object_key} · {row.condition === 'not_present' ? 'Not applicable' : OUTCOMES[row.outcome]}</b>
            {row.geometry && <span> · {row.geometry}</span>}
            {row.condition === 'not_present' ? <>
              <p>The model reported that the guideline condition does not apply to this object. No compliance verdict was counted.</p>
              {row.reason && <details>
                <summary>Raw model explanation</summary>
                <p className="text-text-muted">This explanation may contradict the applicability result. It is retained for review, not treated as an annotation defect.</p>
                <p>{row.reason}</p>
              </details>}
            </> : row.reason && <p>{row.reason}</p>}
            {(row.flags ?? []).length > 0 && <p className="text-text-muted">Review notes: {(row.flags ?? []).join(', ')}</p>}
          </li>
        ))}
        {scope.ineligible.map((row) => (
          <li key={row.object_key}>
            <b>{row.object_key} · Not assessed for this guideline</b>
            <p>{row.detail}</p>
          </li>
        ))}
      </ul>
      {scope.notes.map((note, index) => <p key={index} className="text-2xs text-text-muted">{note}</p>)}
      <p className="text-2xs text-text-muted">Model observations need reviewer confirmation and do not change the annotation.</p>
    </section>
  );
}

export function VisualObservations({ proposal }: { proposal: unknown }) {
  const scope = readScope(proposal);
  if (scope) return <><ScopedObservations scope={scope} /><FeedbackComparison proposal={proposal} /></>;
  const observations = readObservations(proposal);
  if (!observations) return null;
  return (
    <section className="finding-evidence" aria-label="Per-object visual observations">
      <span className="finding-section-label">Per-object visual observations</span>
      <ul className="space-y-2 text-xs">
        {observations.map((row) => (
          <li key={row.object_key}>
            <b>{row.object_key} · {OUTCOMES[row.outcome]}</b>
            {row.geometry.length > 0 && <span> · {row.geometry.join(', ')}</span>}
            <p>{row.reason}</p>
            {row.region && <p className="text-text-muted">Inspect region [{row.region.join(', ')}] in original image pixels, measured from the top left at 0.</p>}
          </li>
        ))}
      </ul>
      <p className="text-2xs text-text-muted">Model observations need reviewer confirmation. Regions locate possible issues and do not change the annotation.</p>
    </section>
  );
}
