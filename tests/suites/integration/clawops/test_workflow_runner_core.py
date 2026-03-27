"""Core workflow runner behavior coverage."""

from __future__ import annotations

import pytest

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


def test_workflow_runner_runs_list_commands_without_shell() -> None:
    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "python",
                    "kind": "shell",
                    "command": ["python3", "-c", "print('ok')"],
                    "timeout": 5,
                }
            ]
        }
    )
    results = runner.run()
    assert results[0].ok is True


def test_workflow_runner_rejects_implicit_shell_for_string_commands() -> None:
    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "unsafe",
                    "kind": "shell",
                    "command": "echo hello",
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="string commands require shell=True"):
        runner.run()


def test_workflow_runner_requires_explicit_context_provider_and_scale() -> None:
    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "context",
                    "kind": "context_pack",
                    "config": "context.yaml",
                    "repo": ".",
                    "query": "review auth",
                }
            ]
        },
        dry_run=True,
    )

    with pytest.raises(TypeError, match="context_pack.provider must be a string"):
        runner.run()
