"""One-attempt feedback comparisons: job-first locks, server-time fencing, no replay."""
from ..models.feedback import FeedbackEvaluationPage
from datetime import timedelta
from uuid import uuid4
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from ..core.feedback import FeedbackError, load_execution_pin
from ..models.feedback import EvaluationInput, FeedbackEvaluation, FeedbackEvaluationCreate, FeedbackEvaluationStarted, FeedbackPolicyActivate, CaseComparison, ComparisonMetrics, checked, fingerprint
from ..models.job import Job, JobKind, Progress, ApiError, ResultReference
from .feedback_repository import FeedbackRepository
from .workflow_repository import _job_row_values, _decode_job, JobClaimLostError, JobAlreadyFinishedError
from .dataset_repository import _pagination, _identifier
from ..services.feedback_metrics import compute_metrics

class FeedbackJobRepository(FeedbackRepository):

    def enqueue(self, request: FeedbackEvaluationCreate) -> FeedbackEvaluationStarted:
        request = checked(FeedbackEvaluationCreate, request)
        with self.connection() as connection:
            policy = self._set(connection, request.policy_id, 'policy')
            truth = self._set(connection, request.ground_truth_id, 'truth')
        execution = (policy.cases[0].model_execution if policy.cases[0].target.rule.check_name == 'pedestrian_box_enclosure'
                     else load_execution_pin())
        if policy.cases[0].target != truth.cases[0].target or policy.cases[0].manifest.dataset_id != truth.cases[0].manifest.dataset_id:
            raise FeedbackError('POLICY_INCOMPATIBLE')
        try:
            inputs = EvaluationInput(policy=policy, truth=truth, runtime=execution, requested_by=request.reviewer_id)
        except ValidationError as error:
            raise FeedbackError('EVALUATION_OVERLAP') from error
        self._verify(policy.cases + truth.cases)
        from ..services.feedback_prompt import prepare_payload
        for case in truth.cases:
            if execution.enclosure is not None:
                from ..services.enclosure_feedback import prepare_comparison
                prepare_comparison(case.manifest, case.target, policy, execution, self.asset_settings)
            else:
                prepare_payload(case.manifest, case.target, policy.cases, execution, self.asset_settings)
        identifier = 'evaluation_' + uuid4().hex
        with self.connection(write=True) as connection:
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            job = Job.queued(job_id='job_' + uuid4().hex, kind=JobKind.FEEDBACK_EVALUATION, total=len(truth.cases), now=now)
            connection.execute("INSERT INTO jobs(job_id,kind,status,progress,result,error,created_at,updated_at,resource_kind,resource_id)\n                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'feedback_evaluation',%s)", _job_row_values(job) + (identifier,))
            connection.execute('INSERT INTO feedback_evaluations(evaluation_id,job_id,policy_id,truth_id,input,input_sha256,created_at)\n                VALUES(%s,%s,%s,%s,%s,%s,%s)', (identifier, job.job_id, policy.resource_id, truth.resource_id, Jsonb(inputs.model_dump(mode='json')), fingerprint(inputs), now))
            public = self._evaluation(connection, identifier)
        return FeedbackEvaluationStarted(evaluation=public, job=job)

    def _input(self, row):
        inputs = self.decode(EvaluationInput, row['input'])
        if fingerprint(inputs) != row['input_sha256'] or inputs.policy.resource_id != row['policy_id'] or inputs.truth.resource_id != row['truth_id']:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        return inputs

    def _evaluation(self, connection, identifier):
        _identifier(identifier, 'evaluation_id')
        row = connection.execute('SELECT e.*,j.status,j.kind,j.resource_kind,j.resource_id,j.progress,j.result,j.error,j.updated_at,j.created_at AS job_created_at FROM feedback_evaluations e JOIN jobs j USING(job_id) WHERE evaluation_id=%s', (identifier,)).fetchone()
        if row is None:
            raise FeedbackError('NOT_FOUND')
        inputs = self._input(row)
        job = _decode_job(dict(row, created_at=row['job_created_at']))
        if job.status.value == 'completed' and (job.result is None or job.result.kind != 'feedback_evaluation' or job.result.id != identifier or (job.result.version is not None) or (job.result.href != '/api/v1/feedback/evaluations/' + identifier)):
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        if row['kind'] != 'feedback_evaluation' or row['resource_kind'] != 'feedback_evaluation' or row['resource_id'] != identifier:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        rows = connection.execute('SELECT ordinal,document FROM feedback_case_results WHERE evaluation_id=%s ORDER BY ordinal', (identifier,)).fetchall()
        results = [self.decode(CaseComparison, r['document']) for r in rows]
        if [r['ordinal'] for r in rows] != list(range(len(rows))) or len(rows) > len(inputs.truth.cases):
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        from ..models.feedback import unit_id
        for case, result in zip(inputs.truth.cases, results):
            if result.case_id != unit_id(case.manifest, case.target) or any((p.prediction.rule_id != case.target.rule.rule_id for p in (result.baseline, result.candidate))):
                raise FeedbackError('DEPENDENCY_UNAVAILABLE')
            from ..services.enclosure_feedback import validate_prediction
            for arm in ('baseline', 'candidate'):
                validate_prediction(getattr(result, arm), case.manifest, case.target, inputs.runtime,
                    inputs.policy if arm == 'candidate' else None)
        progress = self.decode(Progress, row['progress'])
        if progress.total != len(inputs.truth.cases) or progress.processed != len(results):
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        metrics = None if row['metrics'] is None else self.decode(ComparisonMetrics, row['metrics'])
        if row['status'] == 'completed':
            try:
                actual = compute_metrics(inputs, results)
            except ValueError as error:
                raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error
            if actual != metrics:
                raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        elif metrics is not None:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        return FeedbackEvaluation(evaluation_id=identifier, job_id=row['job_id'], policy_id=row['policy_id'], policy_sha256=fingerprint(inputs.policy), ground_truth_id=row['truth_id'], truth_sha256=fingerprint(inputs.truth), execution=inputs.runtime, status=row['status'], created_at=row['created_at'], metrics=metrics, results=results)

    def get_evaluation(self, identifier: str) -> FeedbackEvaluation:
        with self.connection() as connection:
            return self._evaluation(connection, identifier)

    def evaluations(self, *, limit: int=20, offset: int=0) -> FeedbackEvaluationPage:
        _pagination(limit, offset)
        with self.connection() as connection:
            total = connection.execute('SELECT count(*) AS n FROM feedback_evaluations').fetchone()['n']
            rows = connection.execute('SELECT evaluation_id FROM feedback_evaluations ORDER BY created_at DESC,evaluation_id COLLATE "C" LIMIT %s OFFSET %s', (limit, offset)).fetchall()
            items = [self._evaluation(connection, r['evaluation_id']) for r in rows]
        return FeedbackEvaluationPage(items=items, limit=limit, offset=offset, total=total, next_offset=offset + len(items) if offset + len(items) < total else None)

    def activate(self, policy_id: str, request: FeedbackPolicyActivate):
        _identifier(policy_id, 'policy_id')
        request = checked(FeedbackPolicyActivate, request)
        with self.connection(write=True) as connection:
            resource = connection.execute('SELECT job_id FROM feedback_evaluations WHERE evaluation_id=%s', (request.evaluation_id,)).fetchone()
            if resource is None:
                raise FeedbackError('NOT_FOUND')
            connection.execute('SELECT job_id FROM jobs WHERE job_id=%s FOR UPDATE', (resource['job_id'],)).fetchone()
            evaluation = self._evaluation(connection, request.evaluation_id)
            if evaluation.policy_id != policy_id:
                raise FeedbackError('FEEDBACK_CONFLICT')
            if evaluation.status != 'completed':
                raise FeedbackError('FEEDBACK_NOT_READY')
            if any((not p.model_verified or p.prediction.outcome == 'unavailable' for r in evaluation.results for p in (r.baseline, r.candidate))):
                raise FeedbackError('FEEDBACK_NOT_READY')
            policy = self._set(connection, policy_id, 'policy')
            if evaluation.policy_sha256 != fingerprint(policy):
                raise FeedbackError('FEEDBACK_CONFLICT')
            connection.execute('SELECT resource_id FROM feedback_sets WHERE resource_id=%s FOR UPDATE', (policy_id,)).fetchone()
            existing = connection.execute('SELECT * FROM feedback_activations WHERE policy_id=%s', (policy_id,)).fetchone()
            if existing is not None:
                if (existing['evaluation_id'], existing['reviewer_id']) != (request.evaluation_id, request.reviewer_id):
                    raise FeedbackError('FEEDBACK_CONFLICT')
            else:
                connection.execute('INSERT INTO feedback_activations(policy_id,evaluation_id,reviewer_id,created_at) VALUES(%s,%s,%s,clock_timestamp())', (policy_id, request.evaluation_id, request.reviewer_id))
            return self._policy_view(connection, policy)

    def claim(self, worker_id):
        _identifier(worker_id, 'worker_id')
        with self.connection(write=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE kind='feedback_evaluation' AND status='queued' ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if row is None:
                return None
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            token = uuid4().hex
            resource = connection.execute('SELECT * FROM feedback_evaluations WHERE job_id=%s FOR UPDATE', (row['job_id'],)).fetchone()
            if resource is None or row['resource_kind'] != 'feedback_evaluation' or row['resource_id'] != resource['evaluation_id']:
                raise FeedbackError('DEPENDENCY_UNAVAILABLE')
            inputs = self._input(resource)
            public = self._evaluation(connection, resource['evaluation_id'])
            if row['attempt'] != 0 or any(row[key] is not None for key in ('claimed_by', 'claim_token', 'lease_until')) or resource['deadline'] is not None or public.results:
                raise FeedbackError('DEPENDENCY_UNAVAILABLE')
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            progress = Progress(phase='comparing', processed=0, total=len(inputs.truth.cases), fraction=0.0)
            connection.execute(
                "UPDATE jobs SET status='running',progress=%s,claimed_by=%s,claim_token=%s,"
                "lease_until=%s,attempt=attempt+1,updated_at=%s WHERE job_id=%s",
                (Jsonb(progress.model_dump(mode='json')), worker_id, token,
                 now + timedelta(seconds=30), now, row['job_id']),
            )
            connection.execute('UPDATE feedback_evaluations SET deadline=%s WHERE evaluation_id=%s', (now + timedelta(seconds=3600), resource['evaluation_id']))
        return (row['job_id'], worker_id, token, inputs)

    def _locked(self, connection, claim):
        job_id, worker, token, _ = claim
        row = connection.execute('SELECT * FROM jobs WHERE job_id=%s FOR UPDATE', (job_id,)).fetchone()
        now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if row is None or row['kind'] != 'feedback_evaluation' or row['claimed_by'] != worker or (row['claim_token'] != token) or (row['status'] not in ('running', 'cancelling')) or (row['lease_until'] is None) or (row['lease_until'] <= now):
            raise JobClaimLostError('Feedback ownership expired or was replaced.')
        resource = connection.execute('SELECT * FROM feedback_evaluations WHERE job_id=%s FOR UPDATE', (job_id,)).fetchone()
        now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if resource is None or row['lease_until'] <= now or row['resource_kind'] != 'feedback_evaluation' or (row['resource_id'] != resource['evaluation_id']):
            raise JobClaimLostError('Feedback ownership is no longer valid.')
        if resource['deadline'] is None:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        if checked(EvaluationInput, claim[3]) != self._input(resource):
            raise ValueError('Claim payload differs from its durable frozen input.')
        return (row, resource, now)

    def _finish(self, connection, row, status, now, *, metrics=None, recovery=False):
        error = None
        result = None
        if status == 'failed':
            error = ApiError(code='FEEDBACK_EVALUATION_FAILED', message='The comparison could not be completed.', request_id='worker_' + uuid4().hex).model_dump(mode='json')
        if status == 'completed':
            result = ResultReference(kind='feedback_evaluation', id=row['resource_id'], href='/api/v1/feedback/evaluations/' + row['resource_id']).model_dump(mode='json')
        guard = '' if recovery else ' AND claim_token=%s AND claimed_by=%s AND lease_until>clock_timestamp()'
        if status == 'completed':
            guard += ' AND EXISTS (SELECT 1 FROM feedback_evaluations e WHERE e.job_id=jobs.job_id AND e.deadline>clock_timestamp())'
        values = (status, None if error is None else Jsonb(error), None if result is None else Jsonb(result), now, row['job_id'])
        if not recovery:
            values += (row['claim_token'], row['claimed_by'])
        changed = connection.execute('UPDATE jobs SET status=%s,error=%s,result=%s,updated_at=%s WHERE job_id=%s' + guard, values)
        if changed.rowcount != 1:
            raise JobClaimLostError('Feedback ownership expired before publication.')
        if metrics is not None:
            connection.execute('UPDATE feedback_evaluations SET metrics=%s WHERE job_id=%s', (Jsonb(metrics.model_dump(mode='json')), row['job_id']))

    def checkpoint(self, claim) -> bool:
        with self.connection(write=True) as connection:
            row, resource, now = self._locked(connection, claim)
            if row['status'] == 'cancelling':
                self._finish(connection, row, 'cancelled', now)
                return False
            if resource['deadline'] <= now:
                self._finish(connection, row, 'failed', now)
                return False
            changed = connection.execute('UPDATE jobs SET lease_until=%s,updated_at=%s WHERE job_id=%s AND lease_until>clock_timestamp() AND claim_token=%s AND claimed_by=%s', (min(now + timedelta(seconds=30), resource['deadline']), now, row['job_id'], row['claim_token'], row['claimed_by']))
            if changed.rowcount != 1:
                raise JobClaimLostError('Feedback lease expired during renewal.')
        return True

    def save_case(self, claim, ordinal: int, result: CaseComparison) -> bool:
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError('Invalid case ordinal.')
        result = checked(CaseComparison, result)
        with self.connection(write=True) as connection:
            row, resource, now = self._locked(connection, claim)
            if row['status'] == 'cancelling':
                self._finish(connection, row, 'cancelled', now)
                return False
            if resource['deadline'] <= now:
                self._finish(connection, row, 'failed', now)
                return False
            inputs = self._input(resource)
            from ..models.feedback import unit_id
            if ordinal >= len(inputs.truth.cases):
                raise ValueError('Case ordinal outside frozen scope.')
            case = inputs.truth.cases[ordinal]
            if result.case_id != unit_id(case.manifest, case.target) or any((p.prediction.rule_id != case.target.rule.rule_id for p in (result.baseline, result.candidate))):
                raise ValueError('Case result differs from frozen scope.')
            from ..services.enclosure_feedback import validate_prediction
            for arm in ('baseline', 'candidate'):
                validate_prediction(getattr(result, arm), case.manifest, case.target, inputs.runtime,
                    inputs.policy if arm == 'candidate' else None)
            count = connection.execute('SELECT count(*) AS n FROM feedback_case_results WHERE evaluation_id=%s', (resource['evaluation_id'],)).fetchone()['n']
            if count != ordinal:
                raise ValueError('One sequential write per intended case is required.')
            connection.execute('INSERT INTO feedback_case_results(evaluation_id,ordinal,document) VALUES(%s,%s,%s)', (resource['evaluation_id'], ordinal, Jsonb(result.model_dump(mode='json'))))
            progress = Progress(phase='comparing', processed=ordinal + 1, total=len(inputs.truth.cases), fraction=(ordinal + 1) / len(inputs.truth.cases))
            changed = connection.execute('UPDATE jobs SET progress=%s,updated_at=%s WHERE job_id=%s AND lease_until>clock_timestamp() AND claim_token=%s AND claimed_by=%s AND EXISTS (SELECT 1 FROM feedback_evaluations e WHERE e.job_id=jobs.job_id AND e.deadline>clock_timestamp())', (Jsonb(progress.model_dump(mode='json')), now, row['job_id'], row['claim_token'], row['claimed_by']))
            if changed.rowcount != 1:
                raise JobClaimLostError('Feedback ownership/deadline expired before progress publication.')
        return True

    def complete(self, claim):
        with self.connection(write=True) as connection:
            row, resource, now = self._locked(connection, claim)
            if row['status'] == 'cancelling':
                self._finish(connection, row, 'cancelled', now)
                return
            if resource['deadline'] <= now:
                self._finish(connection, row, 'failed', now)
                return
            public = self._evaluation(connection, resource['evaluation_id'])
            metrics = compute_metrics(self._input(resource), public.results)
            row, resource, now = self._locked(connection, claim)
            if row['status'] == 'cancelling':
                self._finish(connection, row, 'cancelled', now)
                return
            if resource['deadline'] <= now:
                self._finish(connection, row, 'failed', now)
                return
            self._finish(connection, row, 'completed', now, metrics=metrics)

    def fail(self, claim):
        with self.connection(write=True) as connection:
            row, resource, now = self._locked(connection, claim)
            self._finish(connection, row, 'cancelled' if row['status'] == 'cancelling' else 'failed', now)

    def _cancel_locked(self, connection, row):
        if row['kind'] != 'feedback_evaluation':
            raise ValueError('Not a feedback job.')
        if row['status'] in ('completed', 'failed', 'cancelled'):
            from ..models.job import JobStatus
            raise JobAlreadyFinishedError(row['job_id'], JobStatus(row['status']))
        resource = connection.execute('SELECT evaluation_id FROM feedback_evaluations WHERE job_id=%s FOR UPDATE', (row['job_id'],)).fetchone()
        if resource is None:
            raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        status = 'cancelled' if row['status'] == 'queued' else 'cancelling'
        connection.execute('UPDATE jobs SET status=%s,updated_at=%s WHERE job_id=%s', (status, now, row['job_id']))
        return _decode_job(connection.execute('SELECT * FROM jobs WHERE job_id=%s', (row['job_id'],)).fetchone())

    def recover(self):
        with self.connection(write=True) as connection:
            rows = connection.execute("SELECT * FROM jobs WHERE kind='feedback_evaluation' AND status IN ('running','cancelling') AND lease_until<=clock_timestamp() FOR UPDATE SKIP LOCKED").fetchall()
            for row in rows:
                now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
                if row['lease_until'] > now:
                    continue
                resource = connection.execute('SELECT evaluation_id FROM feedback_evaluations WHERE job_id=%s FOR UPDATE', (row['job_id'],)).fetchone()
                if resource is None or row['resource_kind'] != 'feedback_evaluation' or row['resource_id'] != resource['evaluation_id']:
                    raise FeedbackError('DEPENDENCY_UNAVAILABLE')
                now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
                self._finish(connection, row, 'cancelled' if row['status'] == 'cancelling' else 'failed', now, recovery=True)
