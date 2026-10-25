import { describe, expect, it } from 'vitest';
import type { components } from '../../api/generated/openapi';
import { getExportDetailPollInterval, getTrackedExportJob } from './exportPolling';

type ExportRecord = components['schemas']['Export'];
const record = (status: ExportRecord['status']): ExportRecord => ({ export_id: 'exp-1', job_id: 'job-1', audit_id: 'audit-1', format: 'penn_fudan', selection: 'accepted_only', include_images: false, include_rechecks: false, status, included_count: null, excluded_count: null, download_url: null, manifest_url: null, created_at: '2026-10-21T00:00:00Z' });

describe('export lifecycle polling', () => {
  it('tracks in-progress exports by their job and detail resource', () => {
    const active = record('running');
    expect(getTrackedExportJob(active)).toBe('job-1');
    expect(getExportDetailPollInterval(active)).toBe(3000);
    expect(getTrackedExportJob(record('completed'))).toBeNull();
    expect(getExportDetailPollInterval(record('completed'))).toBe(false);
  });
});
