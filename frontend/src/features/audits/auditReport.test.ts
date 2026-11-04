import { describe, expect, it } from 'vitest';
import { aggregateByRule, buildReportRows, mapWithConcurrency, reportToCsv, summarizeQueue } from './auditReport';
import { makeFinding, makeSampleAudit, makeSummary } from '../../test/fixtures';

describe('audit report', () => {
  it('summarises tool results and review decisions across the queue', () => {
    const summary = summarizeQueue([
      makeSummary({ sample_id: 'a', tool_status: 'flagged', review_status: 'rejected' }),
      makeSummary({ sample_id: 'b', tool_status: 'no_issue', review_status: 'accepted', incomplete: true }),
      makeSummary({ sample_id: 'c', tool_status: 'no_issue', review_status: 'pending_review' }),
    ]);
    expect(summary.total).toBe(3);
    expect(summary.tool).toMatchObject({ flagged: 1, no_issue: 2 });
    expect(summary.review).toMatchObject({ accepted: 1, rejected: 1, pending_review: 1 });
    expect(summary.incomplete).toBe(1);
  });

  it('aggregates outcomes and reviewer agreement per rule', () => {
    const samples = [
      makeSampleAudit([
        makeFinding({ finding_id: '1', rule_id: 'R1', outcome: 'fail', review: { revision: 1, resolution: 'accepted', reviewer_id: 'ana', comment: '', updated_at: null } }),
        makeFinding({ finding_id: '2', rule_id: 'R2', outcome: 'pass' }),
      ]),
      makeSampleAudit([
        makeFinding({ finding_id: '3', sample_id: 'sample-2', rule_id: 'R1', outcome: 'pass', review: { revision: 1, resolution: 'rejected', reviewer_id: 'ana', comment: 'Box too loose', updated_at: null } }),
      ]),
    ];
    const [r1, r2] = aggregateByRule(samples);
    expect(r1).toMatchObject({ rule_id: 'R1', outcomes: { pass: 1, fail: 1, unsure: 0, unavailable: 0 }, confirmed: 1, overridden: 1, unreviewed: 0 });
    expect(r2).toMatchObject({ rule_id: 'R2', unreviewed: 1 });
  });

  it('builds one report row per finding with rule, result, evidence, citation and decision', () => {
    const rows = buildReportRows([makeSampleAudit([makeFinding()]), makeSampleAudit([], { summary: makeSummary({ sample_id: 'empty' }) })]);
    expect(rows[0]).toMatchObject({
      sample_id: 'sample-1',
      rule_id: 'LBL-1',
      result: 'Fail',
      explanation: 'Label is not in the approved list',
      evidence: 'actual_label=vehicle',
      citation: 'Allowed labels are person.',
      finding_decision: 'Not reviewed',
    });
    expect(rows[1]).toMatchObject({ sample_id: 'empty', result: 'No findings' });
  });

  it('writes CSV with escaping and neutralises spreadsheet formulas', () => {
    const rows = buildReportRows([makeSampleAudit([makeFinding({ description: 'Has "quotes", commas', review: { revision: 1, resolution: 'rejected', reviewer_id: 'x', comment: '=HYPERLINK("bad")', updated_at: null } })])]);
    const csv = reportToCsv(rows);
    const [header, line] = csv.split('\r\n');
    expect(header?.startsWith('sample_id,sample_revision,object_id,rule_id')).toBe(true);
    expect(line).toContain('"Has ""quotes"", commas"');
    expect(line).toContain(`"'=HYPERLINK(""bad"")"`);
  });

  it('never runs more than the concurrency limit at once and keeps order', async () => {
    let running = 0;
    let peak = 0;
    const progress: number[] = [];
    const result = await mapWithConcurrency([5, 1, 4, 2, 3], 2, async (n) => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((resolve) => setTimeout(resolve, n));
      running -= 1;
      return n * 10;
    }, (done) => progress.push(done));
    expect(result).toEqual([50, 10, 40, 20, 30]);
    expect(peak).toBeLessThanOrEqual(2);
    expect(progress).toEqual([1, 2, 3, 4, 5]);
  });
});

it('retains the selected attempt identity in downloaded report rows', () => {
  const rows = buildReportRows([makeSampleAudit([makeFinding({ audit_id: 'recheck-2' })], { audit_id: 'recheck-2' })]);
  expect(rows[0]?.result_audit_id).toBe('recheck-2');
  expect(reportToCsv(rows)).toContain('result_audit_id');
  expect(reportToCsv(rows)).toContain('recheck-2');
});

it('adds the attempt label after the existing columns, keeping the internal ID column', () => {
  const rows = buildReportRows([makeSampleAudit([makeFinding({ audit_id: 'recheck-2' })], { audit_id: 'recheck-2' })],
    (id) => (id === 'recheck-2' ? 'Audit 014 R02' : ''));
  expect(rows[0]?.result_audit_label).toBe('Audit 014 R02');
  const [header, first] = reportToCsv(rows).split('\r\n');
  const columns = header!.split(',');
  // Existing machine-readable columns keep their positions; the label is appended.
  expect(columns.slice(-2)).toEqual(['result_audit_id', 'result_audit_label']);
  expect(first!.split(',').slice(-2)).toEqual(['recheck-2', 'Audit 014 R02']);
  expect(buildReportRows([makeSampleAudit([makeFinding()])])[0]?.result_audit_label).toBe('');
});
