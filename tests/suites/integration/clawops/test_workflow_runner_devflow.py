"""Integration coverage for workflow-runner devflow step kinds."""

from __future__ import annotations

import pathlib

from clawops.devflow_state import begin_run, get_run
from clawops.workflow_runner import WorkflowRunner
from tests.utils.helpers.cli import PathPrepender
from tests.utils.helpers.devflow import (
    init_git_repo,
    install_fake_devflow_backends,
    write_strongclaw_shaped_repo,
)


def test_workflow_runner_executes_devflow_stage_steps(
    tmp_path: pathlib.Path,
    prepend_path: PathPrepender,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir)
    prepend_path(bin_dir)

    journal_db = repo_root / ".clawops" / "op_journal.sqlite"
    begin_run(
        journal_db,
        run_id="df_runner",
        repo_root=repo_root,
        project_id="project-1",
        workspace_id="workspace-1",
        lane="default",
        goal="ship",
        run_profile="production",
        bootstrap_profile="strongclaw",
        workflow_path=repo_root / "workflow.yaml",
        plan_sha256="hash",
        requested_by="tester",
    )
    run_root = repo_root / ".clawops" / "devflow" / "df_runner"
    (run_root / "artifacts" / "architect").mkdir(parents=True, exist_ok=True)
    (run_root / "artifacts" / "architect" / "design.md").write_text("design\n", encoding="utf-8")

    workflow: dict[str, object] = {
        "base_dir": str(repo_root),
        "steps": [
            {
                "name": "start",
                "kind": "stage_record",
                "db": str(journal_db),
                "run_id": "df_runner",
                "stage_name": "architect",
                "stage_index": 0,
                "role": "architect",
                "workspace_root": str(repo_root),
                "status": "running",
            },
            {
                "name": "snapshot",
                "kind": "git_snapshot",
                "workspace": str(repo_root),
            },
            {
                "name": "dispatch",
                "kind": "worker_dispatch",
                "state_dir": str(run_root / "sessions" / "architect"),
                "journal_db": str(journal_db),
                "task": {
                    "project": {"root": str(repo_root)},
                    "workspace": {"kind": "git_worktree", "path": str(repo_root)},
                    "lane": "default",
                    "role": "architect",
                    "backend": "claude",
                    "prompt": "Architect the change",
                    "operation_kind": "devflow-architect",
                    "permissions_mode": "approve-reads",
                    "workspace_mode": "verify_only",
                    "required_auth_mode": "subscription",
                    "expected_artifacts": [
                        {
                            "name": "design",
                            "role": "architect",
                            "path": str(run_root / "artifacts" / "architect" / "design.md"),
                            "required": True,
                        }
                    ],
                },
            },
            {
                "name": "mutation-gate",
                "kind": "git_mutation_gate",
                "workspace": str(repo_root),
                "from_step": "snapshot",
            },
            {
                "name": "artifact-gate",
                "kind": "artifact_gate",
                "from_step": "dispatch",
            },
            {
                "name": "manifest",
                "kind": "artifact_manifest",
                "run_root": str(run_root),
                "manifest": str(run_root / "artifacts" / "manifest.json"),
                "run_id": "df_runner",
                "stage_name": "architect",
                "role": "architect",
                "artifacts": [
                    {
                        "name": "design",
                        "path": "artifacts/architect/design.md",
                        "required": True,
                    }
                ],
            },
        ],
    }

    results = WorkflowRunner(workflow, base_dir=repo_root).run()
    view = get_run(journal_db, run_id="df_runner")

    assert [result.ok for result in results] == [True, True, True, True, True, True]
    assert view.stages[0].status == "running"
    assert (run_root / "artifacts" / "manifest.json").exists()
