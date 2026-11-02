import type { components } from '../../api/generated/openapi';

type Observation = components['schemas']['VisualObservation'];

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

export function VisualObservations({ proposal }: { proposal: unknown }) {
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
