"""Unit tests for deterministic workflows."""

from __future__ import annotations

import pathlib

import pytest

from clawops.workflow_runner import WorkflowRunner, main
from tests.utils.helpers.repo import REPO_ROOT


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


def test_workflow_main_allows_trusted_repo_workflows_in_dry_run() -> None:
    result = main(
        [
            "--workflow",
            str(REPO_ROOT / "platform/configs/workflows/daily_healthcheck.yaml"),
            "--dry-run",
        ]
    )

    assert result == 0


def test_workflow_main_rejects_untrusted_paths_by_default(tmp_path: pathlib.Path) -> None:
    workflow_path = tmp_path / "custom.yaml"
    workflow_path.write_text(
        "name: custom\nsteps:\n  - name: noop\n    kind: journal_init\n    db: ignored.sqlite\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="outside trusted roots"):
        main(["--workflow", str(workflow_path), "--dry-run"])


def test_workflow_main_allows_untrusted_paths_with_explicit_override(
    tmp_path: pathlib.Path,
) -> None:
    workflow_path = tmp_path / "custom.yaml"
    workflow_path.write_text(
        "name: custom\nsteps:\n  - name: noop\n    kind: journal_init\n    db: ignored.sqlite\n",
        encoding="utf-8",
    )

    result = main(
        [
            "--workflow",
            str(workflow_path),
            "--allow-untrusted-workflow",
            "--dry-run",
        ]
    )

    assert result == 0
