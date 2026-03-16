"""Unit tests for deterministic workflows."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import load_yaml, write_yaml
from clawops.workflow_runner import WorkflowRunner, main


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
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    result = main(
        [
            "--workflow",
            str(repo_root / "platform/configs/workflows/daily_healthcheck.yaml"),
            "--dry-run",
        ]
    )

    assert result == 0


def test_workflow_main_rejects_untrusted_paths_by_default(tmp_path: pathlib.Path) -> None:
    workflow_path = tmp_path / "custom.yaml"
    workflow_path.write_text(
        "name: custom\n"
        "steps:\n"
        "  - name: noop\n"
        "    kind: journal_init\n"
        "    db: ignored.sqlite\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="outside trusted roots"):
        main(["--workflow", str(workflow_path), "--dry-run"])


def test_workflow_main_allows_untrusted_paths_with_explicit_override(
    tmp_path: pathlib.Path,
) -> None:
    workflow_path = tmp_path / "custom.yaml"
    workflow_path.write_text(
        "name: custom\n"
        "steps:\n"
        "  - name: noop\n"
        "    kind: journal_init\n"
        "    db: ignored.sqlite\n",
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


def test_workflow_runner_resolves_workflow_base_dir_relative_to_workflow_file(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("def run_review():\n    return 'ok'\n", encoding="utf-8")

    policy_path = repo / "platform" / "configs" / "policy" / "policy.yaml"
    config_path = repo / "platform" / "configs" / "context" / "context-service.yaml"
    workflow_path = tmp_path / "workflows" / "code-review.yaml"

    write_yaml(
        policy_path,
        {
            "defaults": {"decision": "allow"},
            "zones": {
                "coder": {
                    "allow_actions": ["github.comment.create"],
                    "allow_categories": ["external_write"],
                }
            },
            "allowlists": {"github_repo": ["your-org/openclaw-platform"]},
            "approval": {"require_for_actions": ["github.comment.create"]},
        },
    )
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})
    write_yaml(
        workflow_path,
        {
            "name": "code-review",
            "base_dir": "../repo",
            "steps": [
                {"name": "journal", "kind": "journal_init", "db": ".clawops/op_journal.sqlite"},
                {
                    "name": "policy",
                    "kind": "policy_check",
                    "policy": "platform/configs/policy/policy.yaml",
                    "payload": {
                        "trust_zone": "coder",
                        "action": "github.comment.create",
                        "category": "external_write",
                        "target_kind": "github_repo",
                        "target": "your-org/openclaw-platform",
                    },
                },
                {
                    "name": "context",
                    "kind": "context_pack",
                    "config": "platform/configs/context/context-service.yaml",
                    "repo": ".",
                    "query": "run_review",
                },
            ],
        },
    )

    runner = WorkflowRunner(load_yaml(workflow_path), workflow_path=workflow_path)
    results = runner.run()

    assert [item.ok for item in results] == [True, True, True]
    assert (repo / ".clawops" / "op_journal.sqlite").exists()
    assert (repo / ".clawops" / "context.sqlite").exists()


def test_workflow_runner_prefers_explicit_base_dir_over_workflow_base_dir(
    tmp_path: pathlib.Path,
) -> None:
    cli_base_dir = tmp_path / "cli-base"
    workflow_base_dir = tmp_path / "workflow-base"
    cli_base_dir.mkdir()
    workflow_base_dir.mkdir()

    runner = WorkflowRunner(
        {
            "base_dir": "workflow-base",
            "steps": [
                {"name": "journal", "kind": "journal_init", "db": "journal.sqlite"},
            ],
        },
        base_dir=cli_base_dir,
        workflow_path=tmp_path / "workflow.yaml",
    )

    results = runner.run()

    assert results[0].ok is True
    assert (cli_base_dir / "journal.sqlite").exists()
    assert not (workflow_base_dir / "journal.sqlite").exists()
