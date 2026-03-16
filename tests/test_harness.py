"""Unit tests for the harness."""

from __future__ import annotations

import pathlib

from clawops.common import write_yaml
from clawops.harness import run_suite


def test_run_command_case(tmp_path: pathlib.Path) -> None:
    suite = tmp_path / "suite.yaml"
    write_yaml(
        suite,
        {
            "cases": [
                {
                    "id": "echo",
                    "kind": "command",
                    "command": ["python3", "-c", "print('hello')"],
                    "assert": {"exit_code": 0, "stdout_contains": ["hello"]},
                }
            ]
        },
    )
    results = run_suite(suite)
    assert results[0]["passed"] is True
