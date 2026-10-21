"""Call-time configuration and stable public failures for reviewer feedback."""
import os
from dotenv import dotenv_values
from ..models.feedback import ExecutionPin
from .config import DEFAULT_ENV_PATH, load_ollama_settings

class FeedbackError(RuntimeError):
    messages = {'NOT_FOUND': (404, 'The feedback resource does not exist.'), 'FEEDBACK_NOT_READY': (409, 'The reviewed evidence or completed evaluation is not ready.'), 'FEEDBACK_CONFLICT': (409, 'The feedback snapshot or activation no longer matches.'), 'EVALUATION_OVERLAP': (409, 'Examples overlap the held-out evaluation scope.'), 'CONTEXT_NOT_REPRESENTABLE': (409, 'The complete required evidence cannot fit the configured context bounds.'), 'POLICY_INCOMPATIBLE': (409, 'The policy is not active or does not match the confirmed rule scope.'), 'DEPENDENCY_UNAVAILABLE': (503, 'Feedback storage or execution configuration is unavailable.')}

    def __init__(self, code):
        self.code = code
        self.status, message = self.messages[code]
        super().__init__(message)

def load_execution_pin() -> ExecutionPin:
    """Pin a locally configured digest without making a model/server request."""
    try:
        settings = load_ollama_settings()
        values = {**dotenv_values(DEFAULT_ENV_PATH, interpolate=False), **os.environ}
        return ExecutionPin(model=settings.model, model_digest=values['FEEDBACK_MODEL_DIGEST'], num_ctx=settings.context_length, num_predict=min(settings.max_output_tokens, 2048), timeout_seconds=min(600, max(1, int(settings.timeout_seconds))))
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error
