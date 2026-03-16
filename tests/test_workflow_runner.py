"""Unit tests for deterministic workflows."""

from __future__ import annotations

from clawops.workflow_runner import WorkflowRunner


def test_workflow_runner_dry_run() -> None:
    runner = WorkflowRunner(
        {
            "steps": [
                {"name": "shell step", "kind": "shell", "command": "echo hello"},
                {"name": "journal", "kind": "journal_init", "db": "ignored.sqlite"},
            ]
        },
        dry_run=True,
    )
    results = runner.run()
    assert all(item.ok for item in results)
