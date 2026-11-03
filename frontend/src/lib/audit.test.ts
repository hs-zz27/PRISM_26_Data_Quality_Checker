import { describe, expect, it } from 'vitest';
import { unwrapAuditStarted } from './audit';
describe('audit start response', () => {
  it('keeps nested audit and job identities', () => {
    const result = unwrapAuditStarted({ audit: { audit_id: 'a1', job_id: 'j1', dataset_id: 'd1', rulebook_id: 'r1', rulebook_version: 1, source_mode: 'snapshot', status: 'queued', sample_count: 0, processed_count: 0, failed_count: 0, incomplete_count: 0, created_at: 'now', updated_at: 'now', name: null, rulebook_name: null }, job: { job_id: 'j1', kind: 'audit', status: 'queued', progress: { phase: 'queued', processed: 0, total: 1, fraction: 0 }, result: null, error: null, created_at: 'now', updated_at: 'now' }, feedback_policy_id: null, parent_audit_id: null });
    expect(result.audit.audit_id).toBe('a1'); expect(result.job.job_id).toBe('j1');
  });
});
