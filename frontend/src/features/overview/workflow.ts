export interface WorkflowCounts {
  draftRulebooks: number;
  confirmedRulebooks: number;
  readyDatasets: number;
  audits: number;
  completedAudits: number;
  exports: number;
}

export type StepState = 'done' | 'next' | 'todo';

export interface WorkflowStep {
  title: string;
  description: string;
  status: string;
  action: string;
  to: string;
  state: StepState;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** The guideline → rules → data → review → export flow, with the first unfinished step marked "next". */
export function workflowSteps(c: WorkflowCounts): WorkflowStep[] {
  const steps: Omit<WorkflowStep, 'state'>[] = [
    {
      title: 'Paste the guideline',
      description: 'Copy the guideline text from the protected document and paste it. PRISM proposes rules, each citing its exact sentence.',
      status: c.draftRulebooks + c.confirmedRulebooks ? `${plural(c.draftRulebooks + c.confirmedRulebooks, 'rulebook')} so far` : 'No rulebooks yet',
      action: 'Rulebooks',
      to: '/rulebooks',
    },
    {
      title: 'Confirm the rules',
      description: 'Check every proposed rule, add any the tool missed, then lock the rulebook. Nothing is checked until you do.',
      status: c.confirmedRulebooks ? `${plural(c.confirmedRulebooks, 'confirmed rulebook')}` : c.draftRulebooks ? `${plural(c.draftRulebooks, 'draft')} waiting for review` : 'Nothing to confirm yet',
      action: c.draftRulebooks ? 'Review drafts' : 'Rulebooks',
      to: '/rulebooks',
    },
    {
      title: 'Import the data',
      description: 'Upload a ZIP of images and annotations. PRISM keeps an immutable copy so every audit is reproducible.',
      status: c.readyDatasets ? `${plural(c.readyDatasets, 'dataset')} ready` : 'No ready datasets',
      action: 'Datasets',
      to: '/datasets',
    },
    {
      title: 'Audit and review',
      description: 'Run the confirmed rules over the data. The tool pre-ticks what it can; you confirm or override every result, with a reason.',
      status: c.audits ? `${plural(c.audits, 'audit')} · ${c.completedAudits} finished processing` : 'No audits yet',
      action: 'Audits',
      to: '/audits',
    },
    {
      title: 'Export and improve',
      description: 'Export the samples reviewers accepted, and use reviewer decisions to evaluate improvements before they are switched on.',
      status: c.exports ? `${plural(c.exports, 'export')}` : 'No exports yet',
      action: 'Exports',
      to: '/exports',
    },
  ];
  const done = [
    c.draftRulebooks + c.confirmedRulebooks > 0,
    c.confirmedRulebooks > 0,
    c.readyDatasets > 0,
    c.completedAudits > 0,
    c.exports > 0,
  ];
  const nextIndex = done.findIndex((value) => !value);
  return steps.map((step, index) => ({ ...step, state: done[index] ? 'done' : index === nextIndex ? 'next' : 'todo' }));
}
