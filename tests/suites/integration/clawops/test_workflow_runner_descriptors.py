"""Workflow dispatch, descriptor, and gate coverage."""

from __future__ import annotations

import os
import pathlib

import pytest

from clawops.common import write_yaml
from clawops.workflow_runner import WorkflowRunner
from tests.fixtures.cli import write_fake_acpx, write_status_script
from tests.fixtures.context import build_context_project
from tests.fixtures.journal import create_journal


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
    project, workspace, config = build_context_project(tmp_path)
    (workspace / "main.py").write_text("def run_task():\n    return 'ok'\n", encoding="utf-8")
    write_yaml(config, {"index": {"db_path": ".clawops/context.sqlite"}})

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_acpx(bin_dir)
    write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    runner = WorkflowRunner(
        {
            "steps": [
                {
                    "name": "dispatch",
                    "kind": "worker_dispatch",
                    "journal_db": str(tmp_path / "workflow.sqlite"),
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

    journal = create_journal(db_path)
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
