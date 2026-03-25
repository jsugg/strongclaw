"""Reusable policy-file builders for tests."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Mapping

from clawops.common import write_yaml

type PolicyPayload = Mapping[str, object]
type PolicyFactory = Callable[[PolicyPayload, str], pathlib.Path]


def write_policy_file(policy_path: pathlib.Path, payload: PolicyPayload) -> pathlib.Path:
    """Write one policy file and return its path."""
    write_yaml(policy_path, payload)
    return policy_path
