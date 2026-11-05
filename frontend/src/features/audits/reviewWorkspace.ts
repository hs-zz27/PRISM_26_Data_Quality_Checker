import type { components } from '../../api/generated/openapi';
import { bboxToCss, type BBox1Based } from '../../lib/coords';

type Finding = components['schemas']['Finding'];
type Rule = components['schemas']['Rule'];
type SampleAuditSummary = components['schemas']['SampleAuditSummary'];
type SampleReviewDecision = components['schemas']['SampleReviewDecision'];
type FindingResolution = components['schemas']['FindingResolution'];

export type ReviewKeyAction = 'finding-accept' | 'finding-reject' | 'finding-unsure' | 'sample-accept' | 'sample-reject' | 'sample-correction' | 'next' | 'previous' | 'mask' | 'boxes' | 'polygons' | 'labels' | 'fit' | 'help' | null;
export function resolveReviewKey(key: string, shiftKey = false): ReviewKeyAction {
  const normalized = key.toLowerCase();
  if (normalized === 'a') return shiftKey ? 'sample-accept' : 'finding-accept';
  if (normalized === 'r') return shiftKey ? 'sample-reject' : 'finding-reject';
  if (normalized === 'u' && !shiftKey) return 'finding-unsure';
  if (normalized === 'n' && shiftKey) return 'sample-correction';
  if (['j', 'arrowright'].includes(normalized)) return 'next';
  if (['k', 'arrowleft'].includes(normalized)) return 'previous';
  if (normalized === 'm') return 'mask';
  if (normalized === 'b') return 'boxes';
  if (normalized === 'p') return 'polygons';
  if (normalized === 'l') return 'labels';
  if (normalized === 'f') return 'fit';
  if (key === '?') return 'help';
  return null;
}

/**
 * 12-colour palette matching the backend overlay renderer (overlay_render.py).
 * Indexed by `(displayNumber - 1) % 12`.
 */
export const PALETTE = [
  'rgb(230, 25, 75)', 'rgb(60, 180, 75)', 'rgb(0, 130, 200)', 'rgb(245, 130, 48)',
  'rgb(145, 30, 180)', 'rgb(0, 170, 170)', 'rgb(240, 50, 230)', 'rgb(170, 190, 20)',
  'rgb(250, 150, 180)', 'rgb(0, 100, 100)', 'rgb(170, 110, 40)', 'rgb(128, 0, 0)',
] as const;

export function colorFor(displayNumber: number): string {
  const idx = ((displayNumber - 1) % PALETTE.length + PALETTE.length) % PALETTE.length;
  return PALETTE[idx] ?? 'rgb(230, 25, 75)';
}

/** Pick a contrasting text colour for a given background. */
export function textColorFor(displayNumber: number): string {
  const rgb = colorFor(displayNumber);
  const match = rgb.match(/\d+/g);
  if (!match || match.length < 3) return '#fff';
  const r = Number(match[0] ?? 0);
  const g = Number(match[1] ?? 0);
  const b = Number(match[2] ?? 0);
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#000' : '#fff';
}

export function readPaneWidth(side: 'left' | 'right', fallback: number): number {
  const value = Number(localStorage.getItem(`prism.review.${side}Width`));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}
export function persistPaneWidth(side: 'left' | 'right', width: number): number {
  const bounded = side === 'left' ? Math.min(380, Math.max(200, width)) : Math.min(480, Math.max(280, width));
  localStorage.setItem(`prism.review.${side}Width`, String(bounded));
  return bounded;
}

/**
 * Every override must leave a reason (worklet slide 9): overriding a tool finding,
 * rejecting a sample, or sending it back for correction all require a comment.
 */
export function isReasonRequired(
  decision: { kind: 'finding'; resolution: Exclude<FindingResolution, 'pending'> } | { kind: 'sample'; decision: SampleReviewDecision },
): boolean {
  if (decision.kind === 'finding') return decision.resolution === 'rejected';
  return decision.decision === 'rejected' || decision.decision === 'needs_correction';
}

const ATTENTION_ORDER: Record<string, number> = { fail: 0, unsure: 1, unavailable: 2, pass: 3 };

/** Split findings into the checklist sections: needs attention first, then pre-ticked passes. */
export function groupFindings(findings: Finding[]): { attention: Finding[]; passed: Finding[] } {
  const sorted = [...findings].sort((a, b) =>
    (ATTENTION_ORDER[a.outcome] ?? 9) - (ATTENTION_ORDER[b.outcome] ?? 9)
    || a.rule_id.localeCompare(b.rule_id)
    || (a.object_id ?? 0) - (b.object_id ?? 0));
  return {
    attention: sorted.filter((finding) => finding.outcome !== 'pass'),
    passed: sorted.filter((finding) => finding.outcome === 'pass'),
  };
}

/** Confirmed rules that produced no finding for this sample, so the reviewer can still see them. */
export function rulesWithoutFindings(rules: Rule[] | undefined, findings: Finding[]): Rule[] {
  if (!rules) return [];
  const seen = new Set(findings.map((finding) => finding.rule_id));
  return rules.filter((rule) => rule.confirmation_state === 'confirmed' && !seen.has(rule.rule_id));
}

export interface ReviewProgress { reviewed: number; total: number; pending: number; fraction: number }

export function reviewProgress(items: Pick<SampleAuditSummary, 'review_status'>[]): ReviewProgress {
  const total = items.length;
  const pending = items.filter((item) => item.review_status === 'pending_review').length;
  const reviewed = total - pending;
  return { reviewed, total, pending, fraction: total ? reviewed / total : 0 };
}

/** Index of the next sample still awaiting review after `fromIndex`, wrapping around; -1 if none. */
export function nextUnreviewedIndex(items: Pick<SampleAuditSummary, 'review_status'>[], fromIndex: number): number {
  for (let step = 1; step <= items.length; step += 1) {
    const index = (fromIndex + step + items.length) % items.length;
    if (items[index]?.review_status === 'pending_review') return index;
  }
  return -1;
}

export interface BoundedObject {
  bbox: BBox1Based | null;
  coordinate_system?: 'zero_based_continuous';
  polygons?: number[][][];
}

/** Extract bounding coordinates [x0, y0, x1, y1] for an object. */
export function getObjectBounds(obj: BoundedObject): { x0: number; y0: number; x1: number; y1: number } | null {
  if (obj.bbox) {
    if (obj.coordinate_system === 'zero_based_continuous') {
      return { x0: obj.bbox[0], y0: obj.bbox[1], x1: obj.bbox[2], y1: obj.bbox[3] };
    }
    const css = bboxToCss(obj.bbox);
    return { x0: css.left, y0: css.top, x1: css.left + css.width, y1: css.top + css.height };
  }
  if (obj.polygons && obj.polygons.length > 0) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const part of obj.polygons) {
      for (const point of part) {
        const px = point[0];
        const py = point[1];
        if (px !== undefined && py !== undefined) {
          if (px < minX) minX = px;
          if (py < minY) minY = py;
          if (px > maxX) maxX = px;
          if (py > maxY) maxY = py;
        }
      }
    }
    if (isFinite(minX)) return { x0: minX, y0: minY, x1: maxX, y1: maxY };
  }
  return null;
}

export interface CoverageRegion { key: string; objectId: number; label: string; left: number; top: number; width: number; height: number }

/** Where a failed person_coverage finding says the annotation differs from the detected person. */
export function coverageRegions(findings: Finding[], hiddenObjectIds: number[] = []): CoverageRegion[] {
  const regions: CoverageRegion[] = [];
  for (const finding of findings) {
    if (finding.check_name !== 'person_coverage' || finding.outcome !== 'fail' || finding.object_id == null) continue;
    if (hiddenObjectIds.includes(finding.object_id)) continue;
    const result = (finding.evidence?.measurements as Record<string, unknown> | undefined)?.result as Record<string, unknown> | undefined;
    const geometries = Array.isArray(result?.geometries) ? result.geometries : [];
    geometries.forEach((geometry: Record<string, unknown>, g: number) => {
      const oneBased = geometry.coordinate_system === 'one_based_inclusive';
      (Array.isArray(geometry.regions) ? geometry.regions : []).forEach((region: Record<string, unknown>, r: number) => {
        const box = region.box;
        if (!Array.isArray(box) || box.length !== 4 || !box.every((v) => typeof v === 'number')) return;
        const [x0, y0, x1, y1] = box as [number, number, number, number];
        const css = oneBased ? bboxToCss([x0, y0, x1, y1]) : { left: x0, top: y0, width: x1 - x0, height: y1 - y0 };
        regions.push({ key: `${finding.finding_id}-${g}-${r}`, objectId: finding.object_id as number,
          label: `${String(region.kind)} ${String(region.side)} (${String(geometry.geometry)})`, ...css });
      });
    });
  }
  return regions;
}
