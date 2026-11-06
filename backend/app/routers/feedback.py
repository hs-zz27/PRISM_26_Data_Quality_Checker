"""Feedback resource reads/writes; inference runs only in explicit workers."""
from ..models.feedback import FeedbackExamplePage, FeedbackPolicyPage, FeedbackEvaluationPage
from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query, Request
from ..core.feedback import FeedbackError
from ..models.audit_read import ErrorResponse
from ..models.feedback import FeedbackPolicy, FeedbackPolicyCreate, GroundTruth, GroundTruthCreate, FeedbackEvaluation, FeedbackEvaluationCreate, FeedbackEvaluationStarted, FeedbackPolicyActivate
from ..storage.feedback_jobs import FeedbackJobRepository
from .errors import _response
from .exports import _query
router = APIRouter(prefix='/feedback', tags=['Feedback'], responses={404: {'model': ErrorResponse}, 409: {'model': ErrorResponse}, 422: {'model': ErrorResponse}, 500: {'model': ErrorResponse}, 503: {'model': ErrorResponse}})
Id = Annotated[str, Path(min_length=1, max_length=255, pattern=r'\S')]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=9223372036854775807)]

def get_feedback_repository():
    return FeedbackJobRepository()
Repository = Annotated[FeedbackJobRepository, Depends(get_feedback_repository)]

def register_feedback_errors(app):

    async def failure(request: Request, error: FeedbackError):
        return _response(error.status, error.code, str(error))
    app.add_exception_handler(FeedbackError, failure)

@router.get('/examples', response_model=FeedbackExamplePage, operation_id='listFeedbackExamples')
def examples(request: Request, repository: Repository, limit: Limit=20, offset: Offset=0):
    _query(request, {'limit', 'offset'})
    return repository.examples(limit=limit, offset=offset)

@router.post('/policies', response_model=FeedbackPolicy, status_code=201, operation_id='createFeedbackPolicy')
def create_policy(body: FeedbackPolicyCreate, request: Request, repository: Repository):
    _query(request, set())
    return repository.create_policy(body)

@router.get('/policies', response_model=FeedbackPolicyPage, operation_id='listFeedbackPolicies')
def policies(request: Request, repository: Repository, limit: Limit=20, offset: Offset=0):
    _query(request, {'limit', 'offset'})
    return repository.policies(limit=limit, offset=offset)

@router.get('/policies/{policy_id}', response_model=FeedbackPolicy, operation_id='getFeedbackPolicy')
def get_policy(policy_id: Id, request: Request, repository: Repository):
    _query(request, set())
    return repository.get_policy(policy_id)

@router.post('/ground-truth', response_model=GroundTruth, status_code=201, operation_id='createGroundTruth')
def create_truth(body: GroundTruthCreate, request: Request, repository: Repository):
    _query(request, set())
    return repository.create_truth(body)

@router.get('/ground-truth/{ground_truth_id}', response_model=GroundTruth, operation_id='getGroundTruth')
def get_truth(ground_truth_id: Id, request: Request, repository: Repository):
    _query(request, set())
    return repository.get_truth(ground_truth_id)

@router.post('/evaluations', response_model=FeedbackEvaluationStarted, status_code=202, operation_id='evaluateFeedbackPolicy')
def evaluate(body: FeedbackEvaluationCreate, request: Request, repository: Repository):
    _query(request, set())
    return repository.enqueue(body)

@router.get('/evaluations', response_model=FeedbackEvaluationPage, operation_id='listFeedbackEvaluations')
def evaluations(request: Request, repository: Repository, limit: Limit=20, offset: Offset=0):
    _query(request, {'limit', 'offset'})
    return repository.evaluations(limit=limit, offset=offset)

@router.get('/evaluations/{evaluation_id}', response_model=FeedbackEvaluation, operation_id='getFeedbackEvaluation')
def get_evaluation(evaluation_id: Id, request: Request, repository: Repository):
    _query(request, set())
    return repository.get_evaluation(evaluation_id)

@router.post('/policies/{policy_id}/activate', response_model=FeedbackPolicy, operation_id='activateFeedbackPolicy')
def activate(policy_id: Id, body: FeedbackPolicyActivate, request: Request, repository: Repository):
    _query(request, set())
    return repository.activate(policy_id, body)
