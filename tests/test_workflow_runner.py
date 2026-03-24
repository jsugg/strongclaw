"""Unit tests for deterministic workflows."""

from __future__ import annotations

import os
import pathlib

import pytest

from clawops.app_paths import scoped_state_dir
from clawops.common import load_yaml, write_yaml
from clawops.op_journal import OperationJournal
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRONGCLAW_STATE_DIR", str(tmp_path / "state"))
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
    context_pack = scoped_state_dir(repo, category="context-packs") / "context.md"
    assert context_pack.exists()
    assert "run_review" in context_pack.read_text(encoding="utf-8")
    assert results[2].message.endswith(str(context_pack))


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


def _write_status_script(
    bin_dir: pathlib.Path,
    name: str,
    *,
    stdout_text: str,
) -> None:
    target = bin_dir / name
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == *"login status"* ]] || [[ "$*" == *"auth status"* ]]; then\n'
        f"  printf '%s\\n' {stdout_text!r}\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_fake_acpx(bin_dir: pathlib.Path) -> None:
    target = bin_dir / "acpx"
    target.write_text(
        "#!/usr/bin/env bash\n" "set -euo pipefail\n" "printf 'fake-acpx %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def test_workflow_runner_supports_workspace_and_delivery_descriptors(
    tmp_path: pathlib.Path,
) -> None:
    project = tmp_path / "project"
    workspace = project / "workspace"
    bundle = project / "bundle.tar.gz"
    project.mkdir()
    workspace.mkdir()
    bundle.write_text("bundle\n", encoding="utf-8")

    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "workspace",
                    "kind": "workspace_prepare",
                    "project": {"root": str(project)},
                    "workspace": {"kind": "local_dir", "path": str(workspace)},
                },
                {
                    "name": "delivery",
                    "kind": "delivery_prepare",
                    "project": {"root": str(project)},
                    "delivery_target": {
                        "kind": "manual_bundle",
                        "locator": str(bundle),
                    },
                },
            ]
        }
    )

    results = runner.run()

    assert [item.ok for item in results] == [True, True]
    assert pathlib.Path(results[0].details["descriptor_path"]).exists()
    assert pathlib.Path(results[1].details["descriptor_path"]).exists()


def test_workflow_runner_worker_dispatch_and_poll_support_non_git_workspace(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STRONGCLAW_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "project"
    workspace = project / "workspace"
    config = project / "context.yaml"
    project.mkdir()
    workspace.mkdir()
    (workspace / "main.py").write_text("def run_task():\n    return 'ok'\n", encoding="utf-8")
    write_yaml(config, {"index": {"db_path": ".clawops/context.sqlite"}})

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_acpx(bin_dir)
    _write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "dispatch",
                    "kind": "worker_dispatch",
                    "journal_db": "workflow.sqlite",
                    "task": {
                        "project": {"root": str(project)},
                        "workspace": {"kind": "local_dir", "path": str(workspace)},
                        "lane": "feature-a",
                        "role": "developer",
                        "backend": "codex",
                        "prompt": "Implement feature A",
                        "operation_kind": "implement",
                        "required_auth_mode": "subscription",
                        "context": {"config": str(config), "query": "run_task"},
                        "expected_artifacts": [
                            {
                                "name": "implementation-report",
                                "role": "developer",
                                "path": str(project / "report.md"),
                                "required": False,
                            }
                        ],
                    },
                },
                {
                    "name": "poll",
                    "kind": "worker_poll",
                    "dispatch_step": "dispatch",
                },
            ]
        }
    )

    results = runner.run()

    assert [item.ok for item in results] == [True, True]
    assert pathlib.Path(results[0].details["summary_path"]).exists()
    assert pathlib.Path(results[0].details["context_manifest"]).exists()
    assert results[1].message == "succeeded"


def test_workflow_runner_approval_and_artifact_gates(
    tmp_path: pathlib.Path,
) -> None:
    db_path = tmp_path / "workflow.sqlite"
    artifact = tmp_path / "release-notes.md"
    artifact.write_text("done\n", encoding="utf-8")

    journal = OperationJournal(db_path)
    journal.init()
    op = journal.begin(
        scope="session",
        kind="review",
        trust_zone="reviewer",
        normalized_target=str(tmp_path),
        inputs={"foo": "bar"},
    )
    journal.transition(op.op_id, "pending_approval", approval_required=True)

    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "approve",
                    "kind": "approval_gate",
                    "db": str(db_path),
                    "op_id": op.op_id,
                    "approved_by": "operator",
                },
                {
                    "name": "artifacts",
                    "kind": "artifact_gate",
                    "artifacts": [str(artifact)],
                },
            ]
        }
    )

    results = runner.run()

    assert [item.ok for item in results] == [True, True]
    assert results[0].message == "approved"
