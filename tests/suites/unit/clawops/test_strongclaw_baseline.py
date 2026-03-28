"""Tests for the baseline verification workflow."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

import pytest

from clawops import strongclaw_baseline
from clawops.strongclaw_runtime import CommandError
from tests.plugins.infrastructure.context import TestContext


class _FakeCommandResult:
    """Minimal command result stub for baseline workflow tests."""

    def __init__(self, *, ok: bool, stdout: str = "", stderr: str = "") -> None:
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr


class _FakeOpenClawResult:
    """Minimal OpenClaw result stub for baseline workflow tests."""

    ok = True
    stdout = ""
    stderr = ""


def _init_source_checkout(repo_root: pathlib.Path) -> pathlib.Path:
    """Create the minimal StrongClaw marker set for source-checkout validation."""
    repo_root.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        "[project]\nname = 'strongclaw-test'\n", encoding="utf-8"
    )
    (repo_root / "platform").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "clawops").mkdir(parents=True, exist_ok=True)
    return repo_root


def _rendered_openclaw_uses_hypermemory(_path: pathlib.Path) -> bool:
    """Return a deterministic non-hypermemory value for baseline tests."""

    return False


def _noop_harness_smoke(_repo: pathlib.Path, _runs_dir: pathlib.Path) -> None:
    """Provide a typed no-op harness smoke stub for failure-path tests."""


def test_verify_baseline_uses_uv_dependency_group_for_repo_tests(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = _init_source_checkout(tmp_path / "repo")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def _require_openclaw(message: str) -> None:
        del message

    def _resolve_openclaw_config_path(repo: pathlib.Path) -> pathlib.Path:
        assert repo == repo_root
        return config_path

    def _run_openclaw_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        **kwargs: object,
    ) -> _FakeOpenClawResult:
        del arguments, kwargs
        assert repo == repo_root
        return _FakeOpenClawResult()

    def _ensure_model_auth(
        repo: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
    ) -> dict[str, object]:
        del check_only, probe
        assert repo == repo_root
        return {"ok": True}

    def _run_command(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert cwd == repo_root
        commands.append([str(part) for part in command])
        return _FakeCommandResult(ok=True)

    def _run_harness_smoke(repo: pathlib.Path, runs_dir: pathlib.Path) -> None:
        assert repo == repo_root
        assert runs_dir == tmp_path / "runs"

    test_context.patch.patch_object(strongclaw_baseline, "require_openclaw", new=_require_openclaw)
    test_context.patch.patch_object(
        strongclaw_baseline,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "run_openclaw_command",
        new=_run_openclaw_command,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "ensure_model_auth",
        new=_ensure_model_auth,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "rendered_openclaw_uses_hypermemory",
        new=_rendered_openclaw_uses_hypermemory,
    )
    test_context.patch.patch_object(strongclaw_baseline, "run_command", new=_run_command)
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_run_harness_smoke
    )

    payload = strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")

    pytest_command = next(command for command in commands if "pytest" in command)

    assert payload["ok"] is True
    assert "--group" in pytest_command
    assert "dev" in pytest_command
    assert "--extra" not in pytest_command


def test_verify_baseline_surfaces_repo_test_failure_detail(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = _init_source_checkout(tmp_path / "repo")
    config_path = tmp_path / "openclaw.json"
    config_path.write_text("{}", encoding="utf-8")

    def _require_openclaw(message: str) -> None:
        del message

    def _resolve_openclaw_config_path(repo: pathlib.Path) -> pathlib.Path:
        assert repo == repo_root
        return config_path

    def _run_openclaw_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        **kwargs: object,
    ) -> _FakeOpenClawResult:
        del arguments, kwargs
        assert repo == repo_root
        return _FakeOpenClawResult()

    def _ensure_model_auth(
        repo: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
    ) -> dict[str, object]:
        del check_only, probe
        assert repo == repo_root
        return {"ok": True}

    def _run_command(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del cwd, timeout_seconds
        if "pytest" in command:
            return _FakeCommandResult(ok=False, stderr="repo tests failed")
        return _FakeCommandResult(ok=True)

    test_context.patch.patch_object(strongclaw_baseline, "require_openclaw", new=_require_openclaw)
    test_context.patch.patch_object(
        strongclaw_baseline,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "run_openclaw_command",
        new=_run_openclaw_command,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "ensure_model_auth",
        new=_ensure_model_auth,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "rendered_openclaw_uses_hypermemory",
        new=_rendered_openclaw_uses_hypermemory,
    )
    test_context.patch.patch_object(strongclaw_baseline, "run_command", new=_run_command)
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_noop_harness_smoke
    )

    with pytest.raises(CommandError, match="repo tests failed"):
        strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")
