import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { apiGet, apiGetAllPages, apiPost } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { useReviewerStore } from '../../store/reviewerStore';
import { useJobPolling } from '../../hooks/useJobPolling';
import { formatPercent, formatTimestamp } from '../../lib/format';
import { jobStatusLabel, outcomeLabel } from '../../lib/labels';
import { mapWithConcurrency } from '../audits/auditReport';
import { deriveCandidateCases, MAX_GROUND_TRUTH_CASES, MAX_POLICY_EXAMPLES, type CandidateCase } from './groundTruth';
import { selectionBlockReason } from './exampleSelection';
import { ExampleCard } from './ExampleCard';
import { useDatasetName } from '../../hooks/useDatasetName';
import { useDisplayNames } from '../../hooks/useDisplayNames';
import { auditHeading, auditLabel, rulebookLabel } from '../../lib/names';
import type { components } from '../../api/generated/openapi';

type ArmMetrics = components['schemas']['ArmMetrics'];

const ASSESSMENT_VARIANT = { improved: 'pass', equal: 'neutral', regressed: 'fail', inconclusive: 'unsure' } as const;

export const FeedbackPage: React.FC = () => {
  const client = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const datasetName = useDatasetName();
  const [selectedExamples, setSelectedExamples] = useState<string[]>([]);
  const [policyName, setPolicyName] = useState('');
  const [policyId, setPolicyId] = useState('');
  const [truthAuditId, setTruthAuditId] = useState('');
  const [caseEdits, setCaseEdits] = useState<Record<string, Partial<CandidateCase> & { include?: boolean }>>({});
  const [groundTruthId, setGroundTruthId] = useState('');
  const [evaluationId, setEvaluationId] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  const requireReviewer = () => {
    if (!reviewerId.trim()) { openPrompt(); throw new Error('Set your reviewer name first (top right).'); }
    return reviewerId.trim();
  };
  const fail = (error: Error) => setMessage({ tone: 'error', text: error.message });

  const examples = useQuery({ queryKey: queryKeys.feedbackExamples(), queryFn: () => apiGetAllPages('/feedback/examples', { query: { limit: 100, offset: 0 } }) });
  const policies = useQuery({ queryKey: queryKeys.feedbackPolicies(), queryFn: () => apiGetAllPages('/feedback/policies', { query: { limit: 100, offset: 0 } }) });
  const evaluations = useQuery({ queryKey: queryKeys.feedbackEvaluations(), queryFn: () => apiGetAllPages('/feedback/evaluations', { query: { limit: 100, offset: 0 } }) });
  const audits = useQuery({ queryKey: queryKeys.audits(), queryFn: () => apiGetAllPages('/audits', { query: { limit: 100, offset: 0 } }) });
  const selectedTruth = useQuery({ queryKey: queryKeys.groundTruth(groundTruthId), queryFn: () => apiGet('/feedback/ground-truth/{ground_truth_id}', { path: { ground_truth_id: groundTruthId } }), enabled: !!groundTruthId, retry: false });
  const selectedEvaluation = useQuery({ queryKey: queryKeys.feedbackEvaluation(evaluationId), queryFn: () => apiGet('/feedback/evaluations/{evaluation_id}', { path: { evaluation_id: evaluationId } }), enabled: !!evaluationId });
  const job = useJobPolling(jobId, { onSuccess: () => void selectedEvaluation.refetch() });

  // Reviewed findings from a held-out audit become candidate ground-truth cases.
  const truthSource = useQuery({
    queryKey: ['feedback', 'truth-source', truthAuditId],
    queryFn: async () => {
      const queue = await apiGetAllPages('/audits/{audit_id}/samples', { path: { audit_id: truthAuditId }, query: { limit: 100, offset: 0 } });
      const reviewed = queue.items.filter((item) => item.review_status !== 'pending_review');
      const samples = await mapWithConcurrency(reviewed, 4, (item) => apiGet('/audits/{audit_id}/samples/{sample_id}', { path: { audit_id: truthAuditId, sample_id: item.sample_id } }));
      return deriveCandidateCases(samples.map((sample) => ({ ...sample,
        findings: sample.findings.filter((finding) =>
          finding.check_name === 'annotation_guideline' || finding.check_name === 'pedestrian_box_enclosure'),
      })));
    },
    enabled: !!truthAuditId,
  });
  const candidates = useMemo(() => (truthSource.data ?? []).map((c) => {
    const edit = caseEdits[c.key] ?? {};
    const merged = { ...c, ...edit };
    return { ...merged, include: edit.include ?? merged.expected !== null };
  }), [truthSource.data, caseEdits]);
  const chosenCases = candidates.filter((c) => c.include && c.expected && c.reason.trim());
  const editCase = (key: string, patch: Partial<CandidateCase> & { include?: boolean }) => setCaseEdits((old) => ({ ...old, [key]: { ...old[key], ...patch } }));

  const createPolicy = useMutation({
    mutationFn: () => apiPost('/feedback/policies', { body: { name: policyName.trim(), reviewer_id: requireReviewer(), example_ids: selectedExamples } }),
    onSuccess: (policy) => { setPolicyId(policy.policy_id); setSelectedExamples([]); setMessage({ tone: 'ok', text: `Candidate policy ${policy.policy_id} created. Next, freeze held-out ground truth and evaluate it.` }); void client.invalidateQueries({ queryKey: queryKeys.feedbackPolicies() }); },
    onError: fail,
  });
  const createTruth = useMutation({
    mutationFn: () => {
      const reviewer = requireReviewer();
      if (!chosenCases.length) throw new Error('Include at least one case with an expected result and a reason.');
      if (chosenCases.length > MAX_GROUND_TRUTH_CASES) throw new Error(`Ground truth holds at most ${MAX_GROUND_TRUTH_CASES} cases — untick ${chosenCases.length - MAX_GROUND_TRUTH_CASES}.`);
      return apiPost('/feedback/ground-truth', {
        body: {
          reviewer_id: reviewer,
          cases: chosenCases.map((c) => ({ audit_id: c.audit_id, sample_id: c.sample_id, sample_revision: c.sample_revision, rule_id: c.rule_id, expected: c.expected!, reason: c.reason.trim() })),
        },
      });
    },
    onSuccess: (truth) => { setGroundTruthId(truth.ground_truth_id); setMessage({ tone: 'ok', text: `Ground truth ${truth.ground_truth_id} frozen with ${truth.cases.length} cases.` }); },
    onError: fail,
  });
  const evaluate = useMutation({
    mutationFn: () => apiPost('/feedback/evaluations', { body: { policy_id: policyId, policy_version: 1, ground_truth_id: groundTruthId, ground_truth_version: 1, reviewer_id: requireReviewer() } }),
    onSuccess: (result) => { setEvaluationId(result.evaluation.evaluation_id); setJobId(result.job.job_id); setMessage({ tone: 'ok', text: 'Evaluation queued — baseline and candidate are compared on the same held-out cases.' }); void client.invalidateQueries({ queryKey: queryKeys.feedbackEvaluations() }); },
    onError: fail,
  });
  const evaluation = selectedEvaluation.data;
  const activate = useMutation({
    mutationFn: () => {
      const reviewer = requireReviewer();
      if (!evaluation) throw new Error('Select a completed evaluation.');
      if (evaluation.metrics?.assessment !== 'improved' && !window.confirm(`This evaluation was assessed as “${evaluation.metrics?.assessment ?? 'unknown'}”, not “improved”. Activate anyway?`)) throw new Error('Activation cancelled.');
      return apiPost('/feedback/policies/{policy_id}/activate', { path: { policy_id: evaluation.policy_id }, body: { evaluation_id: evaluation.evaluation_id, expected_policy_version: 1, reviewer_id: reviewer } });
    },
    onSuccess: () => { setMessage({ tone: 'ok', text: 'Policy activated. It can now be chosen when launching new audits.' }); void client.invalidateQueries({ queryKey: queryKeys.feedbackPolicies() }); },
    onError: (error) => { if (error.message !== 'Activation cancelled.') fail(error); },
  });

  const exampleItems = examples.data?.items ?? [];
  const exampleNames = useDisplayNames({
    auditIds: exampleItems.map((example) => example.reference.audit_id),
    rulebookIds: exampleItems.map((example) => example.target.rulebook_id),
  });
  const chosenExamples = exampleItems.filter((example) => example.example_id && selectedExamples.includes(example.example_id));
  const selectedGroup = chosenExamples[0];

  return (
    <div className="workstation-page feedback-page">
      <header className="feedback-intro">
        <h1>Improve future audits</h1>
        <p>Reviewed examples provide context for future AI checks. Compare a candidate against the baseline on separate, frozen ground truth before deciding whether to activate it.</p>
        <ol className="feedback-steps">
          <li>Pick examples</li><li>Create candidate</li><li>Freeze held-out truth</li><li>Evaluate</li><li>Activate</li>
        </ol>
      </header>
      {message && <p role={message.tone === 'error' ? 'alert' : 'status'} className={`feedback-message is-${message.tone}`}>{message.text}</p>}

      <section>
        <h2>1. Approved reviewer examples <span className="text-text-faint">(pick up to {MAX_POLICY_EXAMPLES})</span></h2>
        <p className="text-2xs text-text-muted">For the pedestrian box check, examples come from reviewed single-box images, including reasoned overrides. Reviewing a multi-box image does not label each individual box. Evaluation images may have multiple boxes; every box is checked.</p>
        {examples.isLoading ? <p className="text-xs text-text-muted">Loading examples…</p>
          : examples.error ? <p role="alert" className="text-xs text-status-fail">{examples.error.message}</p>
            : !exampleItems.length ? <p className="text-xs text-text-muted">No approved examples yet. They come from reviewed findings of AI-assisted checks.</p>
              : (
                <>
                  <p className="text-2xs text-text-muted">Each card shows the exact image revision the reviewer judged, with all of its annotation boxes outlined. The expected result applies to that whole annotation for the named rule. One policy uses one dataset, one rulebook version and one rule.</p>
                  {selectedGroup && (
                    <p className="example-group" role="status">
                      Selected group: dataset <b>{datasetName(selectedGroup.dataset_id)}</b> · <span className="font-mono">{rulebookLabel(exampleNames.rulebook(selectedGroup.target.rulebook_id), selectedGroup.target.rulebook_version)}</span> · rule <b className="font-mono">{selectedGroup.target.rule.rule_id}</b>. Examples outside this group are disabled.
                      <button type="button" className="rule-action" onClick={() => setSelectedExamples([])}>Clear selection</button>
                    </p>
                  )}
                  <div className="example-grid">
                    {exampleItems.map((example) => {
                      if (!example.example_id) return null;
                      const id = example.example_id;
                      return (
                        <ExampleCard
                          key={example.case_id}
                          example={{ ...example, example_id: id }}
                          datasetName={datasetName(example.dataset_id)}
                          auditLabel={auditLabel(exampleNames.audit(example.reference.audit_id))}
                          rulebookLabel={rulebookLabel(exampleNames.rulebook(example.target.rulebook_id), example.target.rulebook_version)}
                          checked={selectedExamples.includes(id)}
                          blockReason={selectionBlockReason(example, chosenExamples)}
                          onToggle={(checked) => setSelectedExamples((old) => checked ? (old.includes(id) ? old : [...old, id]) : old.filter((x) => x !== id))}
                        />
                      );
                    })}
                  </div>
                </>
              )}
      </section>

      <section>
        <h2>2. Create candidate policy</h2>
        <div className="flex flex-wrap items-center gap-2">
          <input value={policyName} onChange={(event) => setPolicyName(event.target.value)} placeholder="Policy name, e.g. Tight boxes v2" aria-label="Policy name" />
          <Button size="sm" variant="primary" disabled={!policyName.trim() || !selectedExamples.length || createPolicy.isPending} onClick={() => createPolicy.mutate()}>Create from {selectedExamples.length} example(s)</Button>
        </div>
        {policies.data && policies.data.items.length > 0 && (
          <table className="report-table">
            <thead><tr><th>Policy</th><th>Status</th><th>Examples</th><th>Created</th><th /></tr></thead>
            <tbody>
              {policies.data.items.map((policy) => (
                <tr key={policy.policy_id} className={policyId === policy.policy_id ? 'is-selected' : ''}>
                  <td><b>{policy.name}</b> <small className="font-mono">{policy.policy_id}</small></td>
                  <td><Badge size="sm" variant={policy.status === 'active' ? 'pass' : 'pending'}>{policy.status}</Badge>{policy.activated_by && <small> by {policy.activated_by}</small>}</td>
                  <td className="mono">{policy.examples.length}</td>
                  <td>{formatTimestamp(policy.created_at)}</td>
                  <td><button className="rule-action" onClick={() => setPolicyId(policy.policy_id)}>{policyId === policy.policy_id ? 'Selected' : 'Select'}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>3. Freeze held-out ground truth</h2>
        <p className="text-2xs text-text-muted">Choose an audit whose reviewed samples were <b>not</b> used as examples above. Each sample × rule becomes a case; expected results are filled from reviewer decisions and can be changed.</p>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Audit for ground truth" value={truthAuditId} onChange={(event) => { setTruthAuditId(event.target.value); setCaseEdits({}); }}>
            <option value="">Select an audit</option>
            {audits.data?.items.map((audit) => <option key={audit.audit_id} value={audit.audit_id}>{auditHeading(audit.name)} · {datasetName(audit.dataset_id)}</option>)}
          </select>
          {truthSource.isFetching && <span className="text-2xs text-text-muted"><Loader2 className="inline w-3 h-3 animate-spin" /> Loading reviewed findings…</span>}
        </div>
        {truthSource.error && <p role="alert" className="text-xs text-status-fail">{truthSource.error.message}</p>}
        {truthSource.data && !candidates.length && <p className="text-xs text-text-muted">No reviewed samples in this audit yet.</p>}
        {candidates.length > 0 && (
          <>
            <div className="truth-table-wrap">
              <table className="report-table">
                <thead><tr><th>Use</th><th>Sample</th><th>Rule</th><th>Expected</th><th>Reason</th></tr></thead>
                <tbody>
                  {candidates.map((c) => (
                    <tr key={c.key}>
                      <td><input type="checkbox" aria-label={`Use ${c.sample_id} ${c.rule_id}`} checked={c.include} onChange={(event) => editCase(c.key, { include: event.target.checked })} /></td>
                      <td className="font-mono">{c.sample_id} <small>r{c.sample_revision}</small></td>
                      <td><b className="font-mono">{c.rule_id}</b> <small>{c.description}</small></td>
                      <td>
                        <select aria-label={`Expected result for ${c.sample_id} ${c.rule_id}`} value={c.expected ?? ''} onChange={(event) => editCase(c.key, { expected: (event.target.value || null) as CandidateCase['expected'], include: Boolean(event.target.value) })}>
                          <option value="">Not settled</option>
                          <option value="pass">Pass</option>
                          <option value="fail">Fail</option>
                        </select>
                      </td>
                      <td><input aria-label={`Reason for ${c.sample_id} ${c.rule_id}`} value={c.reason} onChange={(event) => editCase(c.key, { reason: event.target.value })} placeholder="Why is this the right answer?" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center gap-3">
              <Button size="sm" variant="primary" disabled={!chosenCases.length || createTruth.isPending} onClick={() => createTruth.mutate()}>Freeze {chosenCases.length} case(s)</Button>
              {chosenCases.length > MAX_GROUND_TRUTH_CASES && <span className="text-2xs text-status-fail">Maximum {MAX_GROUND_TRUTH_CASES} cases.</span>}
            </div>
          </>
        )}
        <label className="correction-field">…or use an existing ground-truth ID
          <input value={groundTruthId} onChange={(event) => setGroundTruthId(event.target.value.trim())} placeholder="ground truth ID" />
        </label>
        {selectedTruth.data && <p className="text-2xs text-text-muted">Using {selectedTruth.data.ground_truth_id}: {selectedTruth.data.cases.length} cases frozen by {selectedTruth.data.reviewer_id} on {formatTimestamp(selectedTruth.data.created_at)}.</p>}
        {selectedTruth.error && <p role="alert" className="text-xs text-status-fail">{selectedTruth.error.message}</p>}
      </section>

      <section>
        <h2>4. Evaluate candidate against baseline</h2>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Policy to evaluate" value={policyId} onChange={(event) => setPolicyId(event.target.value)}>
            <option value="">Select policy</option>
            {policies.data?.items.map((policy) => <option key={policy.policy_id} value={policy.policy_id}>{policy.name} · {policy.status}</option>)}
          </select>
          <Button size="sm" variant="primary" disabled={!policyId || !groundTruthId || evaluate.isPending || job.isActive} isLoading={evaluate.isPending || job.isActive} onClick={() => evaluate.mutate()}>Run evaluation</Button>
          {job.job && <span className="text-2xs text-text-muted">Evaluation {jobStatusLabel(job.job.status)}{job.progress?.total ? ` · ${job.progress.processed}/${job.progress.total}` : ''}</span>}
        </div>
      </section>

      <section className="feedback-results">
        <h2>5. Inspect result and activate</h2>
        <select aria-label="Evaluation" value={evaluationId} onChange={(event) => setEvaluationId(event.target.value)}>
          <option value="">Select evaluation</option>
          {evaluations.data?.items.map((item) => <option key={item.evaluation_id} value={item.evaluation_id}>{item.evaluation_id} · {jobStatusLabel(item.status)} · {formatTimestamp(item.created_at)}</option>)}
        </select>
        {selectedEvaluation.error && <p role="alert" className="text-xs text-status-fail">{selectedEvaluation.error.message}</p>}
        {evaluation && (
          <>
            <p className="text-2xs text-text-muted">Policy <span className="font-mono">{evaluation.policy_id}</span> on ground truth <span className="font-mono">{evaluation.ground_truth_id}</span> · {jobStatusLabel(evaluation.status)}</p>
            {evaluation.metrics ? (
              <>
                <p className="flex items-center gap-2 text-xs">Assessment: <Badge variant={ASSESSMENT_VARIANT[evaluation.metrics.assessment]}>{evaluation.metrics.assessment}</Badge></p>
                <MetricsTable baseline={evaluation.metrics.baseline} candidate={evaluation.metrics.candidate} />
              </>
            ) : <p className="text-xs text-text-muted">Metrics appear when the evaluation completes.</p>}
            {evaluation.results.length > 0 && (
              <details>
                <summary className="text-2xs font-mono uppercase text-text-muted cursor-pointer">Per-case predictions ({evaluation.results.length})</summary>
                <table className="report-table">
                  <thead><tr><th>Rule</th><th>Baseline</th><th>Candidate</th><th>Candidate reason</th></tr></thead>
                  <tbody>
                    {evaluation.results.map((result) => (
                      <tr key={result.case_id}>
                        <td className="font-mono">{result.candidate.prediction.rule_id}</td>
                        <td>{outcomeLabel(result.baseline.prediction.outcome)}</td>
                        <td className={result.candidate.prediction.outcome !== result.baseline.prediction.outcome ? 'text-accent' : ''}>{outcomeLabel(result.candidate.prediction.outcome)}</td>
                        <td>
                          <small>{result.candidate.prediction.reason}</small>
                          {result.candidate.enclosure && (
                            <details>
                              <summary>Per-box results ({result.candidate.enclosure.boxes.length})</summary>
                              <table className="report-table">
                                <thead><tr><th>Box</th><th>Baseline</th><th>Candidate</th><th>Candidate reason</th></tr></thead>
                                <tbody>{result.candidate.enclosure.boxes.map((box, index) => (
                                  <tr key={box.box_number}>
                                    <td>#{box.box_number} · Object {box.object_id}</td>
                                    <td>{outcomeLabel(result.baseline.enclosure?.boxes[index]?.outcome ?? 'unavailable')}</td>
                                    <td>{outcomeLabel(box.outcome)}</td>
                                    <td><small>{box.reason ?? 'No verified result'}</small></td>
                                  </tr>
                                ))}</tbody>
                              </table>
                            </details>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
            <Button size="sm" variant="primary" disabled={evaluation.status !== 'completed' || activate.isPending} onClick={() => activate.mutate()}>Activate this policy for future audits</Button>
          </>
        )}
      </section>
    </div>
  );
};

const METRIC_ROWS: Array<{ key: keyof ArmMetrics; label: string; percent?: boolean }> = [
  { key: 'precision', label: 'Precision (flagged that were real problems)', percent: true },
  { key: 'recall', label: 'Recall (real problems that were flagged)', percent: true },
  { key: 'f1', label: 'F1', percent: true },
  { key: 'coverage', label: 'Coverage (cases with a pass/fail answer)', percent: true },
  { key: 'tp', label: 'True positives' },
  { key: 'fp', label: 'False alarms' },
  { key: 'fn', label: 'Missed problems' },
  { key: 'tn', label: 'True negatives' },
  { key: 'unsure', label: 'Unsure' },
  { key: 'unavailable', label: 'Could not check' },
  { key: 'total_duration_ms', label: 'Total time (ms)' },
];

const MetricsTable: React.FC<{ baseline: ArmMetrics; candidate: ArmMetrics }> = ({ baseline, candidate }) => (
  <table className="report-table metrics-table">
    <thead><tr><th>Measure</th><th>Baseline</th><th>Candidate</th></tr></thead>
    <tbody>
      {METRIC_ROWS.map(({ key, label, percent }) => {
        const format = (value: number | null) => (value === null ? 'n/a' : percent ? formatPercent(value, 1) : Math.round(value).toLocaleString('en-US'));
        const b = baseline[key] as number | null;
        const c = candidate[key] as number | null;
        return (
          <tr key={key}>
            <td>{label}</td>
            <td className="mono">{format(b)}</td>
            <td className={`mono${b !== null && c !== null && c !== b ? ' text-accent' : ''}`}>{format(c)}</td>
          </tr>
        );
      })}
    </tbody>
  </table>
);
