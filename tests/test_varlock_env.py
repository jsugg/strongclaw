"""Regression tests for shipped Varlock environment surfaces."""

from __future__ import annotations

import pathlib


def test_varlock_schema_and_examples_do_not_reference_jira() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    files = (
        repo_root / "platform/configs/varlock/.env.schema",
        repo_root / "platform/configs/varlock/.env.local.example",
        repo_root / "platform/configs/varlock/.env.prod.example",
    )

    for path in files:
        assert "JIRA_" not in path.read_text(encoding="utf-8")
