"""Tests for orchestration descriptors and backend contracts."""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from clawops.backend_registry import (
    PINNED_ACPX_VERSION,
    compatibility_matrix_fixture,
    resolve_backend,
)
from clawops.orchestration import (
    DeliveryTargetDescriptor,
    DescriptorError,
    ProjectDescriptor,
    WorkspaceDescriptor,
    build_lock_identity,
    build_session_identity,
    resolve_orchestration_task,
)


def test_workspace_descriptor_rejects_paths_outside_project_root(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()

    project = ProjectDescriptor.resolve(project_root)

    with pytest.raises(DescriptorError, match="must stay under one of"):
        WorkspaceDescriptor.resolve(project, kind="local_dir", path=outside)


def test_git_workspace_descriptor_derives_branch(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "project"
    repo = project_root / "repo"
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    project = ProjectDescriptor.resolve(project_root)

    workspace = WorkspaceDescriptor.resolve(project, kind="git_clone", path=repo)

    assert workspace.branch == "main"
    assert workspace.kind == "git_clone"


def test_delivery_target_descriptor_supports_manual_bundle(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "project"
    bundle = project_root / "bundle.tar.gz"
    project_root.mkdir()
    bundle.write_text("archive\n", encoding="utf-8")

    project = ProjectDescriptor.resolve(project_root)
    target = DeliveryTargetDescriptor.resolve(
        project,
        kind="manual_bundle",
        locator=str(bundle),
    )

    assert target.kind == "manual_bundle"
    assert target.locator == str(bundle)


def test_orchestration_task_resolution_includes_context_and_artifacts(
    tmp_path: pathlib.Path,
) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "workspace"
    config = project_root / "context.yaml"
    artifact = project_root / "artifacts" / "design.md"
    project_root.mkdir()
    workspace.mkdir()
    config.write_text("index:\n  db_path: .clawops/context.sqlite\n", encoding="utf-8")
    artifact.parent.mkdir()
    artifact.write_text("design\n", encoding="utf-8")

    task = resolve_orchestration_task(
        {
            "project": {"root": str(project_root)},
            "workspace": {"kind": "local_dir", "path": str(workspace)},
            "lane": "delivery",
            "role": "developer",
            "backend": "codex",
            "prompt": "Implement the change",
            "permissions_mode": "approve-all",
            "workspace_mode": "mutable_primary",
            "operation_kind": "implement",
            "artifact_contract_id": "artifact-contract",
            "required_auth_mode": "subscription",
            "allowed_capabilities": ["code.write"],
            "context": {
                "provider": "codebase",
                "scale": "small",
                "config": str(config),
                "query": "implement",
                "prior_artifacts": [str(artifact)],
            },
            "expected_artifacts": [
                {
                    "name": "implementation-report",
                    "role": "developer",
                    "path": str(artifact),
                }
            ],
        },
        base_dir=tmp_path,
    )

    assert task.session_identity == build_session_identity(
        backend="codex",
        project_id=task.project.project_id,
        workspace_id=task.workspace.workspace_id,
        lane="delivery",
        role="developer",
    )
    assert task.lock_identity == build_lock_identity(
        project_id=task.project.project_id,
        workspace_id=task.workspace.workspace_id,
        lane="delivery",
        role="developer",
        operation_kind="implement",
    )
    assert task.context_request is not None
    assert task.context_request.provider == "codebase"
    assert task.context_request.scale == "small"
    assert task.expected_artifacts[0].name == "implementation-report"
    assert task.permissions_mode == "approve-all"
    assert task.workspace_mode == "mutable_primary"
    assert task.artifact_contract_id == "artifact-contract"


def test_backend_registry_exposes_pinned_contract_and_compatibility_fixture() -> None:
    codex = resolve_backend("codex")
    claude = resolve_backend("claude")
    matrix = compatibility_matrix_fixture()

    assert codex.default_auth_mode == "subscription"
    assert claude.supports_auth_mode("cloud-provider") is True
    assert PINNED_ACPX_VERSION == "0.3.0"
    assert matrix["darwin-arm64"]["acpx_version"] == PINNED_ACPX_VERSION
    assert matrix["linux-x86_64"]["acpx_version"] == PINNED_ACPX_VERSION
