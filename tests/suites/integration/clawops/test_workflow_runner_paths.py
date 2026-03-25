"""Path resolution coverage for deterministic workflows."""

from __future__ import annotations

import pathlib

import pytest

from clawops.app_paths import scoped_state_dir
from clawops.common import load_yaml, write_yaml
from clawops.workflow_runner import WorkflowRunner


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
