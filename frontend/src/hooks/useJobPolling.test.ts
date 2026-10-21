import { describe, it, expect } from 'vitest';
import {
  isJobActive,
  isJobTerminal,
  ACTIVE_JOB_STATUSES,
  TERMINAL_JOB_STATUSES,
  getJobPollDelay,
} from './useJobPolling';
import type { JobStatus } from './useJobPolling';

describe('useJobPolling status utilities', () => {
  it('uses bounded backoff delays', () => {
    expect(getJobPollDelay(1000, 0)).toBe(1500);
    expect(getJobPollDelay(1000, 2)).toBe(3375);
    expect(getJobPollDelay(1000, 20)).toBeLessThanOrEqual(15000);
  });
  it('correctly categorizes active job states', () => {
    const activeStates: JobStatus[] = ['queued', 'running', 'cancelling'];
    expect(ACTIVE_JOB_STATUSES).toEqual(activeStates);

    for (const status of activeStates) {
      expect(isJobActive(status)).toBe(true);
      expect(isJobTerminal(status)).toBe(false);
    }
  });

  it('correctly categorizes terminal job states', () => {
    const terminalStates: JobStatus[] = ['completed', 'failed', 'cancelled'];
    expect(TERMINAL_JOB_STATUSES).toEqual(terminalStates);

    for (const status of terminalStates) {
      expect(isJobTerminal(status)).toBe(true);
      expect(isJobActive(status)).toBe(false);
    }
  });
});
