"""Fixtures for the box-grounding runner tests."""

import pytest

from box_helpers import FakeOllama, build_corpus


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path / "corpus")


@pytest.fixture
def fake():
    return FakeOllama()
