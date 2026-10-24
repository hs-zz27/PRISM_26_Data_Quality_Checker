import type { components } from '../../api/generated/openapi';
import { checkLabel, explainFinding, formatValue, outcomeLabel, resolutionLabel, reviewStatusLabel } from '../../lib/labels';

type SampleAudit = components['schemas']['SampleAudit'];
type SampleAuditSummary = components['schemas']['SampleAuditSummary'];
type FindingOutcome = components['schemas']['FindingOutcome'];

export interface QueueSummary {
  total: number;
  tool: Record<'flagged' | 'unsure' | 'no_issue' | 'unavailable' | 'pending', number>;
  review: Record<'pending_review' | 'accepted' | 'rejected' | 'needs_correction', number>;
  incomplete: number;
}

export function summarizeQueue(items: SampleAuditSummary[]): QueueSummary {
  const summary: QueueSummary = {
    total: items.length,
    tool: { flagged: 0, unsure: 0, no_issue: 0, unavailable: 0, pending: 0 },
    review: { pending_review: 0, accepted: 0, rejected: 0, needs_correction: 0 },
    incomplete: 0,
  };
  for (const item of items) {
    summary.tool[item.tool_status] += 1;
    summary.review[item.review_status] += 1;
    if (item.incomplete) summary.incomplete += 1;
  }
  return summary;
}

export interface RuleSummary {
  rule_id: string;
  description: string;
  check_name: string | null;
  origin: string;
  source_sentence: string | null;
  outcomes: Record<FindingOutcome, number>;
  confirmed: number;
  overridden: number;
  unsure: number;
  unreviewed: number;
}

/** Per-rule pass/fail and reviewer agreement across all loaded samples. */
export function aggregateByRule(samples: SampleAudit[]): RuleSummary[] {
  const byRule = new Map<string, RuleSummary>();
  for (const sample of samples) {
    for (const finding of sample.findings) {
      let row = byRule.get(finding.rule_id);
      if (!row) {
        row = {
          rule_id: finding.rule_id,
          description: finding.description,
          check_name: finding.check_name ?? null,
          origin: finding.origin,
          source_sentence: finding.source_sentence ?? null,
          outcomes: { pass: 0, fail: 0, unsure: 0, unavailable: 0 },
          confirmed: 0,
          overridden: 0,
          unsure: 0,
          unreviewed: 0,
        };
        byRule.set(finding.rule_id, row);
      }
      row.outcomes[finding.outcome] += 1;
      const resolution = finding.review?.resolution ?? 'pending';
      if (resolution === 'accepted') row.confirmed += 1;
      else if (resolution === 'rejected') row.overridden += 1;
      else if (resolution === 'unsure') row.unsure += 1;
      else row.unreviewed += 1;
    }
  }
  return [...byRule.values()].sort((a, b) => a.rule_id.localeCompare(b.rule_id));
}

/** One row per finding — the five explainability fields plus the human decision. */
export interface ReportRow {
  sample_id: string;
  sample_revision: number;
  object_id: string;
  rule_id: string;
  rule: string;
  check: string;
  result: string;
  explanation: string;
  evidence: string;
  citation: string;
  rule_origin: string;
  finding_decision: string;
  finding_reviewer: string;
  finding_comment: string;
  sample_decision: string;
  sample_reviewer: string;
  sample_comment: string;
}

export function buildReportRows(samples: SampleAudit[]): ReportRow[] {
  const rows: ReportRow[] = [];
  for (const sample of samples) {
    const sampleReview = sample.review;
    const base = {
      sample_id: sample.summary.sample_id,
      sample_revision: sample.summary.sample_revision,
      sample_decision: reviewStatusLabel(sampleReview?.decision ?? 'pending_review'),
      sample_reviewer: sampleReview?.reviewer_id ?? '',
      sample_comment: sampleReview?.comment ?? '',
    };
    for (const finding of sample.findings) {
      rows.push({
        ...base,
        object_id: finding.object_id === null || finding.object_id === undefined ? '' : String(finding.object_id),
        rule_id: finding.rule_id,
        rule: finding.description,
        check: finding.check_name ? checkLabel(finding.check_name) : 'Manual review',
        result: outcomeLabel(finding.outcome),
        explanation: explainFinding(finding),
        evidence: Object.entries(finding.evidence?.measurements ?? {}).map(([k, v]) => `${k}=${formatValue(v)}`).join('; '),
        citation: finding.source_sentence ?? (finding.origin === 'reviewer' ? '(reviewer-authored rule)' : ''),
        rule_origin: finding.origin,
        finding_decision: resolutionLabel(finding.review?.resolution ?? 'pending'),
        finding_reviewer: finding.review?.reviewer_id ?? '',
        finding_comment: finding.review?.comment ?? '',
      });
    }
    if (sample.findings.length === 0) {
      rows.push({
        ...base, object_id: '', rule_id: '', rule: '', check: '', result: 'No findings', explanation: '',
        evidence: '', citation: '', rule_origin: '', finding_decision: '', finding_reviewer: '', finding_comment: '',
      });
    }
  }
  return rows;
}

const CSV_COLUMNS: Array<keyof ReportRow> = [
  'sample_id', 'sample_revision', 'object_id', 'rule_id', 'rule', 'check', 'result', 'explanation', 'evidence',
  'citation', 'rule_origin', 'finding_decision', 'finding_reviewer', 'finding_comment', 'sample_decision',
  'sample_reviewer', 'sample_comment',
];

function csvCell(value: string | number): string {
  const text = String(value);
  // Prefix formula-like cells so spreadsheets do not execute them.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function reportToCsv(rows: ReportRow[]): string {
  return [CSV_COLUMNS.join(','), ...rows.map((row) => CSV_COLUMNS.map((column) => csvCell(row[column])).join(','))].join('\r\n');
}

/** Run `task` over `items` with at most `limit` in flight, reporting progress. */
export async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  task: (item: T) => Promise<R>,
  onProgress?: (done: number) => void,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  let done = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await task(items[index] as T);
      done += 1;
      onProgress?.(done);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/** Trigger a browser download for generated text content. */
export function downloadText(filename: string, content: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
