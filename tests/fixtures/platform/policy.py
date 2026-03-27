"""Pytest fixtures for policy-file builders."""

from __future__ import annotations

import pathlib

import pytest

from tests.utils.helpers.policy import PolicyFactory, PolicyPayload, write_policy_file


@pytest.fixture
def policy_factory(tmp_path: pathlib.Path) -> PolicyFactory:
    """Return a factory for isolated policy files."""

    def _factory(payload: PolicyPayload, name: str = "policy.yaml") -> pathlib.Path:
        return write_policy_file(tmp_path / name, payload)

    return _factory


__all__ = [
    "policy_factory",
]
