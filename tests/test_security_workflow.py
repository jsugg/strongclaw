"""Regression tests for the security workflow UX."""

from __future__ import annotations

import pathlib


def test_security_workflow_publishes_coverage_job_summary() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/security.yml").read_text(encoding="utf-8")

    assert "name: Coverage summary" in workflow
    assert "coverage.xml" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
