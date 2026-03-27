"""Tests for context envelope assembly and validation."""

from __future__ import annotations

import pathlib

import pytest

from clawops.context.codebase.service import service_from_config
from clawops.context_envelope import (
    ContextEnvelopeBuilder,
    ContextEnvelopeValidationError,
    load_context_envelope,
    validate_context_envelope,
)
from clawops.orchestration import ProjectDescriptor, WorkspaceDescriptor
from tests.utils.helpers.context import build_context_repo


def _build_service(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    return build_context_repo(
        tmp_path,
        files={"app.py": "def greet():\n    return 'hi'\n"},
    )


def test_context_envelope_reuses_identical_inputs(tmp_path: pathlib.Path) -> None:
    repo, config = _build_service(tmp_path)
    service = service_from_config(config, repo, scale="small")
    project = ProjectDescriptor.resolve(repo)
    workspace = WorkspaceDescriptor.resolve(project, kind="local_dir", path=repo)
    builder = ContextEnvelopeBuilder(
        service,
        project=project,
        workspace=workspace,
        lane="default",
        role="developer",
        backend="codex",
        provider="codebase",
        scale="small",
    )

    first = builder.build(query="greet")
    second = builder.build(query="greet")

    assert first.manifest.context_provider == "codebase"
    assert first.manifest.context_scale == "small"
    assert first.manifest.retrieval_modes == ("lexical",)
    assert first.body_path == second.body_path
    assert second.reused is True
    assert first.manifest.context_provider == "codebase"
    assert first.manifest.context_scale == "small"
    assert first.manifest.retrieval_modes == ("lexical",)
    validate_context_envelope(second, service=service, workspace=workspace)


def test_context_envelope_persists_diff_when_inputs_change(tmp_path: pathlib.Path) -> None:
    repo, config = _build_service(tmp_path)
    service = service_from_config(config, repo, scale="small")
    project = ProjectDescriptor.resolve(repo)
    workspace = WorkspaceDescriptor.resolve(project, kind="local_dir", path=repo)
    builder = ContextEnvelopeBuilder(
        service,
        project=project,
        workspace=workspace,
        lane="default",
        role="developer",
        backend="codex",
        provider="codebase",
        scale="small",
    )

    first = builder.build(query="greet")
    (repo / "app.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    second = builder.build(query="greet")

    assert second.body_path != first.body_path
    assert second.diff_path is not None
    diff_payload = load_context_envelope(second.manifest_path).diff_path
    assert diff_payload == second.diff_path
    diff_text = second.diff_path.read_text(encoding="utf-8")
    assert "changed_paths" in diff_text


def test_context_envelope_validation_fails_when_workspace_file_disappears(
    tmp_path: pathlib.Path,
) -> None:
    repo, config = _build_service(tmp_path)
    service = service_from_config(config, repo, scale="small")
    project = ProjectDescriptor.resolve(repo)
    workspace = WorkspaceDescriptor.resolve(project, kind="local_dir", path=repo)
    builder = ContextEnvelopeBuilder(
        service,
        project=project,
        workspace=workspace,
        lane="default",
        role="developer",
        backend="codex",
        provider="codebase",
        scale="small",
    )

    envelope = builder.build(query="greet")
    (repo / "app.py").unlink()

    with pytest.raises(ContextEnvelopeValidationError, match="context file is missing"):
        validate_context_envelope(envelope, service=service, workspace=workspace)
