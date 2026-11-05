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
        # New audits scope each requirement to the annotations it can be checked against (v5).
        # v3/v4 stay selectable for comparison; audits already pinned to v4 or earlier are unaffected
        # because their stored pin, not this default, governs rechecks.
        version = values.get('FEEDBACK_PROMPT_VERSION') or 'annotation-guideline-v5'
        # Focused protocols stay opt-in until their real-model results have been reviewed.
        if version not in {'annotation-guideline-v3', 'annotation-guideline-v4', 'annotation-guideline-v5',
                           'annotation-guideline-v6', 'annotation-guideline-v7'}:
            raise ValueError('Unsupported FEEDBACK_PROMPT_VERSION.')
        return ExecutionPin(
            prompt_version=version,
            # v6/v7 render per-target focused views; older selectable protocols share the full overlay.
            preprocessing='target-focus-overlay-v1' if version in {'annotation-guideline-v6', 'annotation-guideline-v7'}
                          else 'original-and-annotation-overlay-v1',
            model=settings.model, model_digest=values['FEEDBACK_MODEL_DIGEST'],
            num_ctx=settings.context_length, num_predict=min(settings.max_output_tokens, 2048),
            num_thread=settings.cpu_threads,
            timeout_seconds=min(600, max(1, int(settings.timeout_seconds))),
        )
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise FeedbackError('DEPENDENCY_UNAVAILABLE') from error
