"""Tests for the baseline verification workflow."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Mapping, cast

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
    captured_env: dict[str, str] | None = None

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
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert cwd == repo_root
        assert env is not None
        nonlocal captured_env
        captured_env = dict(env)
        commands.append([str(part) for part in command])
        return _FakeCommandResult(ok=True)

    def _run_managed_clawops_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert repo == repo_root
        assert cwd == repo_root
        commands.append(["clawops", *[str(part) for part in arguments]])
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
        strongclaw_baseline,
        "run_managed_clawops_command",
        new=_run_managed_clawops_command,
    )
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_run_harness_smoke
    )

    payload = strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")

    pytest_command = next(command for command in commands if "pytest" in command)

    assert payload["ok"] is True
    assert "--group" in pytest_command
    assert "dev" in pytest_command
    assert "--extra" not in pytest_command
    assert "--ignore" in pytest_command
    ignore_flag_index = pytest_command.index("--ignore")
    assert pytest_command[ignore_flag_index + 1] == str(
        repo_root / "tests/suites/contracts/repo/launch_readiness"
    )
    assert captured_env is not None
    assert pathlib.Path(captured_env["HOME"]).parent == repo_root / ".tmp"
    assert captured_env["XDG_CONFIG_HOME"] == f"{captured_env['HOME']}/.config"
    for isolated_key in (
        "VARLOCK_LOCAL_ENV_FILE",
        "VARLOCK_ENV_DIR",
        "STRONGCLAW_RUNTIME_ROOT",
        "STRONGCLAW_CONFIG_DIR",
        "STRONGCLAW_DATA_DIR",
        "STRONGCLAW_STATE_DIR",
        "STRONGCLAW_LOG_DIR",
        "STRONGCLAW_MEMORY_CONFIG_DIR",
        "STRONGCLAW_VARLOCK_ENV_PATH",
        "STRONGCLAW_VARLOCK_ENV_MODE",
        "OPENCLAW_VARLOCK_ENV_MODE",
    ):
        assert isolated_key not in captured_env


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
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del cwd, env, timeout_seconds
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


def test_verify_baseline_defaults_to_runtime_platform_checks(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    """Baseline verify should probe platform runtime surfaces by default."""
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
        del check_only
        assert repo == repo_root
        return {"ok": True, "probe": probe}

    def _run_managed_clawops_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert cwd == repo_root
        assert repo == repo_root
        command = ["clawops", *[str(part) for part in arguments]]
        commands.append(command)
        if "status" in arguments:
            return _FakeCommandResult(ok=True, stdout="{}")
        return _FakeCommandResult(ok=True)

    def _run_command(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del env, timeout_seconds
        assert cwd == repo_root
        commands.append([str(part) for part in command])
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
    test_context.patch.patch_object(
        strongclaw_baseline,
        "run_managed_clawops_command",
        new=_run_managed_clawops_command,
    )
    test_context.patch.patch_object(strongclaw_baseline, "run_command", new=_run_command)
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_noop_harness_smoke
    )

    payload = strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")
    model_payload = cast(dict[str, object], payload["modelAuth"])

    platform_commands = [
        command for command in commands if command[:2] == ["clawops", "verify-platform"]
    ]

    assert payload["degraded"] is False
    assert payload["verificationMode"] == "runtime"
    assert model_payload["probe"] is True
    assert platform_commands == [
        ["clawops", "verify-platform", "sidecars"],
        ["clawops", "verify-platform", "observability"],
        ["clawops", "verify-platform", "channels"],
        ["clawops", "verify-platform", "browser-lab"],
    ]


def test_verify_baseline_degraded_mode_marks_payload_and_skips_runtime_probes(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    """Degraded baseline mode should stay explicit in both commands and payload."""
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
        del check_only
        assert repo == repo_root
        return {"ok": True, "probe": probe}

    def _run_managed_clawops_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert cwd == repo_root
        assert repo == repo_root
        command = ["clawops", *[str(part) for part in arguments]]
        commands.append(command)
        if "status" in arguments:
            return _FakeCommandResult(ok=True, stdout="{}")
        return _FakeCommandResult(ok=True)

    def _run_command(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del env, timeout_seconds
        assert cwd == repo_root
        commands.append([str(part) for part in command])
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
    test_context.patch.patch_object(
        strongclaw_baseline,
        "run_managed_clawops_command",
        new=_run_managed_clawops_command,
    )
    test_context.patch.patch_object(strongclaw_baseline, "run_command", new=_run_command)
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_noop_harness_smoke
    )

    payload = strongclaw_baseline.verify_baseline(
        repo_root,
        runs_dir=tmp_path / "runs",
        degraded=True,
    )
    model_payload = cast(dict[str, object], payload["modelAuth"])

    platform_commands = [
        command for command in commands if command[:2] == ["clawops", "verify-platform"]
    ]

    assert payload["degraded"] is True
    assert payload["verificationMode"] == "degraded"
    assert model_payload["probe"] is False
    assert "Runtime probes were skipped" in str(payload["guidance"])
    assert platform_commands == [
        ["clawops", "verify-platform", "sidecars", "--skip-runtime"],
        ["clawops", "verify-platform", "observability", "--skip-runtime"],
        ["clawops", "verify-platform", "channels"],
        ["clawops", "verify-platform", "browser-lab", "--skip-runtime"],
    ]


def test_verify_baseline_exclude_browser_lab_omits_browser_lab_target(
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

    def _run_managed_clawops_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert repo == repo_root
        assert cwd == repo_root
        commands.append(["clawops", *[str(part) for part in arguments]])
        return _FakeCommandResult(ok=True)

    def _run_command(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del command, cwd, env, timeout_seconds
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
    test_context.patch.patch_object(
        strongclaw_baseline,
        "run_managed_clawops_command",
        new=_run_managed_clawops_command,
    )
    test_context.patch.patch_object(strongclaw_baseline, "run_command", new=_run_command)
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_noop_harness_smoke
    )

    payload = strongclaw_baseline.verify_baseline(
        repo_root,
        runs_dir=tmp_path / "runs",
        exclude_browser_lab=True,
    )

    platform_commands = [
        command for command in commands if command[:2] == ["clawops", "verify-platform"]
    ]
    assert payload["includeBrowserLab"] is False
    assert payload["excludeBrowserLab"] is True
    assert platform_commands == [
        ["clawops", "verify-platform", "sidecars"],
        ["clawops", "verify-platform", "observability"],
        ["clawops", "verify-platform", "channels"],
    ]


def test_verify_baseline_reindexes_hypermemory_before_verify_when_status_is_dirty(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = _init_source_checkout(tmp_path / "repo")
    config_path = tmp_path / "openclaw.json"
    hypermemory_config_path = tmp_path / "hypermemory.yaml"
    config_path.write_text("{}", encoding="utf-8")
    hypermemory_config_path.write_text("storage:\n  db_path: /tmp/h.sqlite\n", encoding="utf-8")
    commands: list[list[str]] = []
    status_calls = 0

    def _require_openclaw(message: str) -> None:
        del message

    def _resolve_openclaw_config_path(repo: pathlib.Path) -> pathlib.Path:
        assert repo == repo_root
        return config_path

    def _rendered_openclaw_uses_hypermemory(_path: pathlib.Path) -> bool:
        return True

    def _rendered_openclaw_hypermemory_config_path(_path: pathlib.Path) -> pathlib.Path:
        return hypermemory_config_path

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

    def _run_managed_clawops_command(
        repo: pathlib.Path,
        arguments: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del timeout_seconds
        assert repo == repo_root
        assert cwd == repo_root
        command = ["clawops", *[str(part) for part in arguments]]
        commands.append(command)
        nonlocal status_calls
        if command == [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "status",
            "--json",
        ]:
            status_calls += 1
            payload = (
                {
                    "backendActive": "qdrant_sparse_dense_hybrid",
                    "dirty": True,
                    "vectorItems": 0,
                    "sparseVectorItems": 0,
                }
                if status_calls == 1
                else {
                    "backendActive": "qdrant_sparse_dense_hybrid",
                    "dirty": False,
                    "vectorItems": 42,
                    "sparseVectorItems": 42,
                }
            )
            return _FakeCommandResult(ok=True, stdout=json.dumps(payload))
        if command == [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "index",
            "--json",
        ]:
            return _FakeCommandResult(ok=True, stdout="{}")
        if command == [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "verify",
            "--json",
        ]:
            return _FakeCommandResult(ok=True, stdout='{"ok": true}')
        return _FakeCommandResult(ok=True)

    def _run_command(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> _FakeCommandResult:
        del command, timeout_seconds
        assert cwd == repo_root
        assert env is not None
        return _FakeCommandResult(ok=True)

    test_context.patch.patch_object(strongclaw_baseline, "require_openclaw", new=_require_openclaw)
    test_context.patch.patch_object(
        strongclaw_baseline,
        "resolve_openclaw_config_path",
        new=_resolve_openclaw_config_path,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "rendered_openclaw_uses_hypermemory",
        new=_rendered_openclaw_uses_hypermemory,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "rendered_openclaw_hypermemory_config_path",
        new=_rendered_openclaw_hypermemory_config_path,
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
        "run_managed_clawops_command",
        new=_run_managed_clawops_command,
    )
    test_context.patch.patch_object(strongclaw_baseline, "run_command", new=_run_command)
    test_context.patch.patch_object(
        strongclaw_baseline, "run_harness_smoke", new=_noop_harness_smoke
    )

    payload = strongclaw_baseline.verify_baseline(repo_root, runs_dir=tmp_path / "runs")

    assert payload["ok"] is True
    hypermemory_commands = [
        command for command in commands if command[:2] == ["clawops", "hypermemory"]
    ]
    assert hypermemory_commands[:4] == [
        [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "status",
            "--json",
        ],
        [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "index",
            "--json",
        ],
        [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "status",
            "--json",
        ],
        [
            "clawops",
            "hypermemory",
            "--config",
            str(hypermemory_config_path),
            "verify",
            "--json",
        ],
    ]


def test_main_honors_env_mode_and_exclude_browser_lab(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    repo_root = _init_source_checkout(tmp_path / "repo")
    requested_modes: list[str] = []
    exclude_values: list[bool] = []

    @contextmanager
    def _use_varlock_env_mode(env_mode: str) -> Iterator[None]:
        requested_modes.append(env_mode)
        yield

    def _verify_baseline(
        requested_repo_root: pathlib.Path,
        *,
        runs_dir: pathlib.Path,
        degraded: bool = False,
        exclude_browser_lab: bool = False,
    ) -> dict[str, object]:
        del runs_dir, degraded
        assert requested_repo_root == repo_root
        exclude_values.append(exclude_browser_lab)
        return {"ok": True}

    test_context.patch.patch_object(
        strongclaw_baseline,
        "use_varlock_env_mode",
        new=_use_varlock_env_mode,
    )
    test_context.patch.patch_object(
        strongclaw_baseline,
        "verify_baseline",
        new=_verify_baseline,
    )

    exit_code = strongclaw_baseline.main(
        [
            "--source-root",
            str(repo_root),
            "verify",
            "--env-mode",
            "legacy",
            "--exclude-browser-lab",
        ]
    )

    assert exit_code == 0
    assert requested_modes == ["legacy"]
    assert exclude_values == [True]
