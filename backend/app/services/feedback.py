"""Model proposals augment, never rewrite, deterministic measurements or reviews."""
from ..core.feedback import FeedbackError,load_execution_pin
from ..models.feedback import FeedbackRuntime,RulePin,checked,fingerprint
from ..models.finding import FindingOutcome
from ..services.evaluation import RuleEvaluationResult,_make_finding
from ..inference.annotation_guideline import GuidelineClient
from ..storage.feedback_repository import FeedbackRepository


def prepare_runtime(connection,request,book,*,settings):
    """No inference. Persist a complete serializable execution/policy pin at acceptance."""
    supported=[r for r in book.rules if r.check_name=='annotation_guideline']
    runtime=None if request.feedback_runtime is None else checked(FeedbackRuntime,request.feedback_runtime)
    if not supported or request.source_mode.value!='snapshot':
        if request.feedback_policy_id is not None or runtime is not None:raise FeedbackError('POLICY_INCOMPATIBLE')
        return request
    if request.feedback_policy_id is not None:
        repository=FeedbackRepository(settings=settings)
        policy=repository._set(connection,request.feedback_policy_id,'policy')
        active=connection.execute('SELECT * FROM feedback_activations WHERE policy_id=%s',(policy.resource_id,)).fetchone()
        if active is None: raise FeedbackError('POLICY_INCOMPATIBLE')
        from ..models.feedback import EvaluationInput
        row=connection.execute('SELECT e.*,j.status FROM feedback_evaluations e JOIN jobs j USING(job_id) WHERE evaluation_id=%s',(active['evaluation_id'],)).fetchone()
        if row is None:raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        frozen=repository.decode(EvaluationInput,row['input'])
        if fingerprint(frozen)!=row['input_sha256'] or row['status']!='completed':raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        if frozen.policy!=policy:raise FeedbackError('DEPENDENCY_UNAVAILABLE')
        pinned=FeedbackRuntime(execution=frozen.runtime,policy=policy)
        if runtime is not None and runtime!=pinned:raise FeedbackError('POLICY_INCOMPATIBLE')
        target=policy.cases[0].target
        if (policy.cases[0].manifest.dataset_id!=request.dataset_id
                or (target.rulebook_id,target.rulebook_version)!=(book.rulebook_id,book.version)
                or target.rule not in supported):raise FeedbackError('POLICY_INCOMPATIBLE')
        runtime=pinned
    elif runtime is not None:
        if runtime.policy is not None:raise FeedbackError('POLICY_INCOMPATIBLE')
    else:
        # Absence of an owner-pinned model keeps feature-disabled books unavailable,
        # not magically passing and not dependent on an Ollama server.
        import os
        from dotenv import dotenv_values
        from ..core.config import DEFAULT_ENV_PATH
        values={**dotenv_values(DEFAULT_ENV_PATH,interpolate=False),**os.environ}
        if not values.get('FEEDBACK_MODEL_DIGEST'):return request
        runtime=FeedbackRuntime(execution=load_execution_pin())
    return request.model_copy(update={'feedback_runtime':runtime})


def augment_evaluation(evaluation,rulebook,manifest,runtime,asset_settings,*,audit_id,sample_id,sample_revision):
    if runtime is None: return evaluation
    runtime=checked(FeedbackRuntime,runtime)
    targets=[r for r in rulebook.rules if r.check_name=='annotation_guideline']
    if not targets: return evaluation
    findings=[f for f in evaluation.findings if f.check_name!='annotation_guideline']
    client=GuidelineClient(runtime.execution)
    try:
        for rule in targets:
            target=RulePin(rulebook_id=rulebook.rulebook_id,rulebook_version=rulebook.version,rule=rule)
            examples=[]
            if runtime.policy is not None and runtime.policy.cases[0].target==target:
                examples=runtime.policy.cases
            recorded=client.predict(manifest,target,examples,asset_settings)
            findings.append(_make_finding(rule=rule,outcome=FindingOutcome(recorded.prediction.outcome),
                reason=recorded.prediction.reason,object_id=None,occurrence='sample',
                evidence={'model_proposal':recorded.model_dump(mode='json'),
                    'execution':runtime.execution.model_dump(mode='json'),
                    'policy_id':None if not examples else runtime.policy.resource_id,
                    'policy_sha256':None if not examples else fingerprint(runtime.policy),
                    'image_sha256':manifest.original_image.sha256,'annotation_sha256':manifest.annotation_text.sha256},
                audit_id=audit_id,sample_id=sample_id,sample_revision=sample_revision,
                rulebook_id=rulebook.rulebook_id,rulebook_version=rulebook.version))
    finally: client.close()
    return RuleEvaluationResult(findings=findings,internal_check_failures=evaluation.internal_check_failures)
