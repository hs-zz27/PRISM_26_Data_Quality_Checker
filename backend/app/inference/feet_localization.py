"""One bounded image-only localization call, with pre/post local-model verification."""
import json
import time

from pydantic import ValidationError

from .annotation_guideline import GuidelineClient, InferenceUnavailable, unique_object
from ..models.feet_localization import FeetLocalizationPin, FeetLocalizationRecord, FeetObservation
from ..services.feet_localization import prepare_feet_localization


class FeetLocalizationClient:
    """Explicit experiment; never invoked by the generic audit or feedback router."""

    def __init__(self, pin: FeetLocalizationPin, *, transport=None):
        self.pin = pin
        self.transport = GuidelineClient(pin.transport_settings(), transport=transport)

    def close(self):
        self.transport.close()

    def localize(self, image: bytes, target_description: str) -> FeetLocalizationRecord:
        payload, metadata = prepare_feet_localization(image, target_description, self.pin)
        began = time.monotonic()
        content = None
        validation_errors = []
        try:
            self.transport.verify()
            content = self.transport._chat_content(payload)
            try:
                json.loads(content, object_pairs_hook=unique_object)
                observation = FeetObservation.model_validate_json(content)
                # Validate identity, completeness and original-image bounds before post-verification.
                record = FeetLocalizationRecord(**metadata, model_verified=True,
                    observation=observation, duration_ms=0)
            except (ValidationError, ValueError, UnicodeError, RecursionError) as error:
                if isinstance(error, ValidationError):
                    validation_errors = [f"{item['loc']}: {item['type']}"[:200]
                                         for item in error.errors(include_input=False, include_url=False)[:8]]
                else:
                    validation_errors = [type(error).__name__]
                raise InferenceUnavailable('invalid_localization') from error
            self.transport.verify()
            return record.model_copy(update={'duration_ms': (time.monotonic() - began) * 1000})
        except InferenceUnavailable as error:
            return FeetLocalizationRecord(**metadata, model_verified=False, failure=str(error),
                response_excerpt=(content[:2000].replace('\0', '').encode('utf-8', errors='replace').decode('utf-8')
                                  if content is not None else None),
                validation_errors=validation_errors,
                duration_ms=(time.monotonic() - began) * 1000)
