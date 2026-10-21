import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiGetAllPages, apiPost } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Button } from '../../components/ui/Button';
import { useReviewerStore } from '../../store/reviewerStore';
import { useJobPolling } from '../../hooks/useJobPolling';
import type { components } from '../../api/generated/openapi';

type Adjudication = components['schemas']['Adjudication'];

export const FeedbackPage: React.FC = () => {
  const client = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const [selectedExamples, setSelectedExamples] = useState<string[]>([]);
  const [policyName, setPolicyName] = useState('');
  const [groundTruthId, setGroundTruthId] = useState('');
  const [groundTruthCases, setGroundTruthCases] = useState('[]');
  const [policyId, setPolicyId] = useState('');
  const [evaluationId, setEvaluationId] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  const examples = useQuery({ queryKey: queryKeys.feedbackExamples(), queryFn: () => apiGetAllPages('/feedback/examples', { query: { limit: 100, offset: 0 } }) });
  const policies = useQuery({ queryKey: queryKeys.feedbackPolicies(), queryFn: () => apiGetAllPages('/feedback/policies', { query: { limit: 100, offset: 0 } }) });
  const evaluations = useQuery({ queryKey: queryKeys.feedbackEvaluations(), queryFn: () => apiGetAllPages('/feedback/evaluations', { query: { limit: 100, offset: 0 } }) });
  const selectedPolicy = useQuery({ queryKey: queryKeys.feedbackPolicy(policyId), queryFn: () => apiGet('/feedback/policies/{policy_id}', { path: { policy_id: policyId } }), enabled: !!policyId });
  const selectedTruth = useQuery({ queryKey: queryKeys.groundTruth(groundTruthId), queryFn: () => apiGet('/feedback/ground-truth/{ground_truth_id}', { path: { ground_truth_id: groundTruthId } }), enabled: !!groundTruthId });
  const selectedEvaluation = useQuery({ queryKey: queryKeys.feedbackEvaluation(evaluationId), queryFn: () => apiGet('/feedback/evaluations/{evaluation_id}', { path: { evaluation_id: evaluationId } }), enabled: !!evaluationId });
  const job = useJobPolling(jobId);

  const createPolicy = useMutation({
    mutationFn: () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Reviewer ID required.'); }
      return apiPost('/feedback/policies', { body: { name: policyName.trim(), reviewer_id: reviewerId.trim(), example_ids: selectedExamples } });
    },
    onSuccess: (policy) => { setPolicyId(policy.policy_id); setMessage(`Draft policy ${policy.policy_id} created.`); void client.invalidateQueries({ queryKey: queryKeys.feedbackPolicies() }); },
    onError: (error) => setMessage(error.message),
  });
  const createTruth = useMutation({
    mutationFn: () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Reviewer ID required.'); }
      let cases: Adjudication[];
      try { cases = JSON.parse(groundTruthCases) as Adjudication[]; } catch { throw new Error('Cases must be valid JSON.'); }
      if (!Array.isArray(cases) || !cases.length) throw new Error('Add at least one explicitly adjudicated case.');
      return apiPost('/feedback/ground-truth', { body: { reviewer_id: reviewerId.trim(), cases } });
    },
    onSuccess: (truth) => { setGroundTruthId(truth.ground_truth_id); setMessage(`Frozen ground truth ${truth.ground_truth_id} created.`); },
    onError: (error) => setMessage(error.message),
  });
  const evaluate = useMutation({
    mutationFn: () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Reviewer ID required.'); }
      return apiPost('/feedback/evaluations', { body: { policy_id: policyId, policy_version: 1, ground_truth_id: groundTruthId, ground_truth_version: 1, reviewer_id: reviewerId.trim() } });
    },
    onSuccess: (result) => { setEvaluationId(result.evaluation.evaluation_id); setJobId(result.job.job_id); setMessage(`Evaluation queued: ${result.evaluation.evaluation_id}.`); void client.invalidateQueries({ queryKey: queryKeys.feedbackEvaluations() }); },
    onError: (error) => setMessage(error.message),
  });
  const activate = useMutation({
    mutationFn: () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Reviewer ID required.'); }
      return apiPost('/feedback/policies/{policy_id}/activate', { path: { policy_id: policyId }, body: { evaluation_id: evaluationId, expected_policy_version: 1, reviewer_id: reviewerId.trim() } });
    },
    onSuccess: () => { setMessage('Evaluated policy activated for future audits.'); void client.invalidateQueries({ queryKey: queryKeys.feedbackPolicies() }); },
    onError: (error) => setMessage(error.message),
  });

  return <div className="workstation-page feedback-page p-6 max-w-5xl mx-auto space-y-5">
    <h1 className="text-xl font-semibold">Feedback policy evaluation</h1>
    <p>Approved examples → draft policy → frozen adjudications → held-out evaluation → explicit activation.</p>
    {message && <p role="status">{message}</p>}
    <section className="space-y-2"><h2>1. Approved correction examples</h2>{examples.isLoading ? <p>Loading examples…</p> : examples.error ? <p role="alert">{examples.error.message}</p> : !examples.data?.items.length ? <p>No approved examples available.</p> : examples.data.items.map((example) => example.example_id ? <label key={example.case_id} className="block"><input type="checkbox" checked={selectedExamples.includes(example.example_id)} onChange={(event) => setSelectedExamples((old) => event.target.checked ? [...old, example.example_id!] : old.filter((id) => id !== example.example_id))} /> {example.reference.rule_id} · {example.expected} · {example.reason}</label> : null)}</section>
    <section className="space-y-2"><h2>2. Create candidate policy</h2><input value={policyName} onChange={(event) => setPolicyName(event.target.value)} placeholder="Policy name" /><Button disabled={!policyName.trim() || !selectedExamples.length || createPolicy.isPending} onClick={() => createPolicy.mutate()}>Create policy</Button></section>
    <section className="space-y-2"><h2>3. Freeze reviewer adjudications</h2><p>Enter verified case pins and expected pass/fail outcomes. The API has no ground-truth list route.</p><textarea rows={8} className="w-full font-mono" value={groundTruthCases} onChange={(event) => setGroundTruthCases(event.target.value)} aria-label="Ground truth adjudication cases JSON" /><Button disabled={createTruth.isPending} onClick={() => createTruth.mutate()}>Create frozen ground truth</Button><input value={groundTruthId} onChange={(event) => setGroundTruthId(event.target.value)} placeholder="Ground truth ID" />{selectedTruth.data && <p>{selectedTruth.data.cases.length} cases · reviewer {selectedTruth.data.reviewer_id}</p>}</section>
    <section className="space-y-2"><h2>4. Evaluate candidate</h2><select value={policyId} onChange={(event) => setPolicyId(event.target.value)}><option value="">Select policy</option>{policies.data?.items.map((policy) => <option key={policy.policy_id} value={policy.policy_id}>{policy.name} · {policy.status}</option>)}</select><Button disabled={!policyId || !groundTruthId || evaluate.isPending} onClick={() => evaluate.mutate()}>Run evaluation</Button>{job.job && <p>Job {job.job.job_id}: {job.job.status}</p>}</section>
    <section className="space-y-2"><h2>5. Inspect measured result</h2><select value={evaluationId} onChange={(event) => setEvaluationId(event.target.value)}><option value="">Select evaluation</option>{evaluations.data?.items.map((evaluation) => <option key={evaluation.evaluation_id} value={evaluation.evaluation_id}>{evaluation.evaluation_id} · {evaluation.status}</option>)}</select>{selectedEvaluation.data && <><p>Status: {selectedEvaluation.data.status}</p>{selectedEvaluation.data.metrics ? <pre>{JSON.stringify(selectedEvaluation.data.metrics, null, 2)}</pre> : <p>Metrics not available yet.</p>}<pre>{JSON.stringify(selectedEvaluation.data.results, null, 2)}</pre></>}</section>
    <section className="space-y-2"><h2>6. Activate explicitly</h2><Button disabled={!evaluationId || !policyId || selectedEvaluation.data?.status !== 'completed' || activate.isPending} onClick={() => activate.mutate()}>Activate evaluated policy</Button></section>
    {selectedPolicy.error && <p role="alert">{selectedPolicy.error.message}</p>}{selectedTruth.error && <p role="alert">{selectedTruth.error.message}</p>}{selectedEvaluation.error && <p role="alert">{selectedEvaluation.error.message}</p>}
  </div>;
};
