"""Offline request-budget regressions; no model calls or external dataset assets."""
import pytest

from backend.app.models.feedback import FocusedAssessment
from backend.app.services.focused_prompt import FOCUS_IMAGE_COUNT, focused_system
from backend.app.services.prompt_encoding import compact_schema, text_fits
from backend.tests.applicability_fixtures import v6_pin


@pytest.mark.parametrize('feedback_bytes', [0, 500])
def test_recorded_rule7_text_size_fits_16k_without_relaxing_reservations(feedback_bytes):
    # The 2026-11-03 real RULE-7 request carried 5,519 UTF-8 bytes of
    # captions + document (65 polygon vertices and all five context lines).
    # Reproduce its SIZE, not private imagery or a model's answer. With the
    # previous system text this was 9,790 bytes against a 9,728-byte allowance.
    content = 'x' * (5519 + feedback_bytes)
    execution = v6_pin(num_ctx=16384, num_predict=2048)
    schema = compact_schema(FocusedAssessment.model_json_schema())
    assert text_fits(focused_system(), content, schema, execution, FOCUS_IMAGE_COUNT)


def test_oversized_focused_text_is_still_rejected():
    execution = v6_pin(num_ctx=16384, num_predict=2048)
    schema = compact_schema(FocusedAssessment.model_json_schema())
    assert not text_fits(focused_system(), 'x' * 16384, schema, execution, FOCUS_IMAGE_COUNT)
