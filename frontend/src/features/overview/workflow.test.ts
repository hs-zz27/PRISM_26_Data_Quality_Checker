import { describe, expect, it } from 'vitest';
import { workflowSteps } from './workflow';

const empty = { draftRulebooks: 0, confirmedRulebooks: 0, readyDatasets: 0, audits: 0, completedAudits: 0, exports: 0 };

describe('overview workflow', () => {
  it('starts with pasting the guideline', () => {
    expect(workflowSteps(empty).map((s) => s.state)).toEqual(['next', 'todo', 'todo', 'todo', 'todo']);
  });

  it('points at confirming rules when only drafts exist', () => {
    const steps = workflowSteps({ ...empty, draftRulebooks: 2 });
    expect(steps.map((s) => s.state)).toEqual(['done', 'next', 'todo', 'todo', 'todo']);
    expect(steps[1]?.status).toBe('2 drafts waiting for review');
    expect(steps[1]?.action).toBe('Review drafts');
  });

  it('marks the first unfinished step even if later steps are done', () => {
    const steps = workflowSteps({ ...empty, confirmedRulebooks: 1, completedAudits: 1, audits: 1 });
    expect(steps.map((s) => s.state)).toEqual(['done', 'done', 'next', 'done', 'todo']);
  });
});
