"""Shared, cached builds of the API contract for the contract tests.

Building the contract takes about 2 seconds and many tests only read it, so each is built once per test
session. Every call returns a private copy, so a test may change what it gets without affecting others.
"""
from copy import deepcopy
from functools import cache

from backend.api import build_contract as builder
from backend.app.main import create_app


@cache
def _contract():
    return builder.build_contract()


@cache
def _artifacts():
    return builder.render_artifacts()


@cache
def _live():
    return create_app().openapi()


def build_contract():
    return deepcopy(_contract())


def render_artifacts():
    return dict(_artifacts())


def live_openapi():
    return deepcopy(_live())
