"""Tests for the baseline verification workflow."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

import pytest

from clawops import strongclaw_baseline
from clawops.strongclaw_runtime import CommandError


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


def _rendered_openclaw_uses_hypermemory(_path: pathlib.Path) -> bool:
    """Return a deterministic non-hypermemory value for baseline tests."""

    return False


def _noop_harness_smoke(_repo: pathlib.Path, _runs_dir: pathlib.Path) -> None:
    """Provide a typed no-op harness smoke stub for failure-path tests."""


def test_verify_baseline_uses_uv_dependency_group_for_repo_tests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
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

    monkeypatch.setattr(strongclaw_baseline, "require_openclaw", _require_openclaw)
    monkeypatch.setattr(
        strongclaw_baseline,
        "resolve_openclaw_config_path",
        _resolve_openclaw_config_path,
    )
    monkeypatch.setattr(
        strongclaw_baseline,
        "run_openclaw_command",
        _run_openclaw_command,
    )
    monkeypatch.setattr(
        strongclaw_baseline,
        "ensure_model_auth",
        _ensure_model_auth,
    )
    monkeypatch.setattr(
        strongclaw_baseline,
        "rendered_openclaw_uses_hypermemory",
        _rendered_openclaw_uses_hypermemory,
    )
    monkeypatch.setattr(strongclaw_baseline, "run_command", _run_command)
    monkeypatch.setattr(strongclaw_baseline, "run_harness_smoke", _run_harness_smoke)

    payload = strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")

    pytest_command = next(command for command in commands if "pytest" in command)

    assert payload["ok"] is True
    assert "--group" in pytest_command
    assert "dev" in pytest_command
    assert "--extra" not in pytest_command


def test_verify_baseline_surfaces_repo_test_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
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

    monkeypatch.setattr(strongclaw_baseline, "require_openclaw", _require_openclaw)
    monkeypatch.setattr(
        strongclaw_baseline,
        "resolve_openclaw_config_path",
        _resolve_openclaw_config_path,
    )
    monkeypatch.setattr(
        strongclaw_baseline,
        "run_openclaw_command",
        _run_openclaw_command,
    )
    monkeypatch.setattr(
        strongclaw_baseline,
        "ensure_model_auth",
        _ensure_model_auth,
    )
    monkeypatch.setattr(
        strongclaw_baseline,
        "rendered_openclaw_uses_hypermemory",
        _rendered_openclaw_uses_hypermemory,
    )
    monkeypatch.setattr(strongclaw_baseline, "run_command", _run_command)
    monkeypatch.setattr(strongclaw_baseline, "run_harness_smoke", _noop_harness_smoke)

    with pytest.raises(CommandError, match="repo tests failed"):
        strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")
