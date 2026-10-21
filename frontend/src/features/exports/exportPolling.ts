import type { components } from '../../api/generated/openapi';

type ExportRecord = components['schemas']['Export'];

export function getTrackedExportJob(record: ExportRecord): string | null {
  return ['queued', 'running', 'cancelling'].includes(record.status) ? record.job_id : null;
}

export function getExportDetailPollInterval(record: ExportRecord): number | false {
  return getTrackedExportJob(record) ? 3000 : false;
}
