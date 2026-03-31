"""Workflow dispatch, descriptor, and gate coverage."""

from __future__ import annotations

import pathlib

from clawops.common import write_yaml
from clawops.typed_values import as_mapping, as_string
from clawops.workflow_runner import WorkflowRunner
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.cli import write_fake_acpx, write_status_script
from tests.utils.helpers.context import build_context_project
from tests.utils.helpers.journal import create_journal


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
    workspace_descriptor_path = pathlib.Path(
        as_string(results[0].details["descriptor_path"], path="results[0].details.descriptor_path")
    )
    delivery_descriptor_path = pathlib.Path(
        as_string(results[1].details["descriptor_path"], path="results[1].details.descriptor_path")
    )

    assert [item.ok for item in results] == [True, True]
    assert workspace_descriptor_path.exists()
    assert delivery_descriptor_path.exists()


def test_workflow_runner_worker_dispatch_and_poll_support_non_git_workspace(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    test_context.env.apply_profile(
        "workflow_state",
        overrides={"STRONGCLAW_STATE_DIR": tmp_path / "state"},
    )
    project, workspace, config = build_context_project(tmp_path)
    (workspace / "main.py").write_text("def run_task():\n    return 'ok'\n", encoding="utf-8")
    write_yaml(config, {"index": {"db_path": ".clawops/context.sqlite"}})

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_acpx(bin_dir)
    write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    test_context.env.prepend_path(bin_dir)

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
                        "context": {
                            "provider": "codebase",
                            "scale": "small",
                            "config": str(config),
                            "query": "run_task",
                        },
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
    summary_path = pathlib.Path(
        as_string(results[0].details["summary_path"], path="results[0].details.summary_path")
    )
    context_manifest_path = pathlib.Path(
        as_string(
            results[0].details["context_manifest"],
            path="results[0].details.context_manifest",
        )
    )

    assert [item.ok for item in results] == [True, True]
    assert summary_path.exists()
    assert context_manifest_path.exists()
    assert results[1].message == "succeeded"


def test_workflow_runner_worker_dispatch_writes_review_packet_when_approval_is_required(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    test_context.env.apply_profile(
        "workflow_state",
        overrides={"STRONGCLAW_STATE_DIR": tmp_path / "state"},
    )
    project, workspace, config = build_context_project(tmp_path)
    (workspace / "main.py").write_text("def run_task():\n    return 'ok'\n", encoding="utf-8")
    write_yaml(config, {"index": {"db_path": ".clawops/context.sqlite"}})

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_acpx(bin_dir)
    write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    test_context.env.prepend_path(bin_dir)

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
                        "approval_required": True,
                        "context": {
                            "provider": "codebase",
                            "scale": "small",
                            "config": str(config),
                            "query": "run_task",
                        },
                    },
                }
            ]
        }
    )

    results = runner.run()

    assert len(results) == 1
    assert results[0].ok is False
    assert "approval required before dispatch" in results[0].message
    review_artifact_path = pathlib.Path(
        as_string(
            results[0].details["review_artifact_path"],
            path="results[0].details.review_artifact_path",
        )
    )
    dispatch = as_mapping(results[0].details["dispatch"], path="results[0].details.dispatch")
    assert dispatch["dispatched"] is True
    assert review_artifact_path.exists()


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
