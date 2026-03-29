"""Tests for StrongClaw operational compose-state wiring."""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

import pytest

from clawops import strongclaw_ops
from clawops.strongclaw_runtime import ExecResult
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT


class _ComposeStateDir(Protocol):
    """Typed callable contract for the compose state helper."""

    def __call__(self, repo_root: pathlib.Path, *, repo_local_state: bool) -> pathlib.Path: ...


class _ComposeEnv(Protocol):
    """Typed callable contract for the compose env helper."""

    def __call__(
        self,
        repo_root: pathlib.Path,
        *,
        repo_local_state: bool,
        compose_name: str,
    ) -> dict[str, str]: ...


_compose_state_dir = cast(
    _ComposeStateDir,
    cast(Any, strongclaw_ops)._compose_state_dir,
)
_compose_env = cast(
    _ComposeEnv,
    cast(Any, strongclaw_ops)._compose_env,
)
_compose_path = cast(
    Callable[[pathlib.Path, str], pathlib.Path],
    cast(Any, strongclaw_ops)._compose_path,
)


def _exec_result(
    *argv: str,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> ExecResult:
    """Build a typed subprocess result for StrongClaw runtime helpers."""
    return ExecResult(
        argv=argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
    )


def _fixed_compose_env(
    _repo_root: pathlib.Path,
    *,
    repo_local_state: bool,
    compose_name: str,
) -> dict[str, str]:
    """Return a minimal compose environment for command-sequencing tests."""
    del repo_local_state, compose_name
    return {"PATH": "/usr/bin"}


def _identity_varlock_command(
    _repo_root: pathlib.Path,
    command: Sequence[str],
) -> list[str]:
    """Return the raw command for tests that do not need Varlock wrapping."""
    return [str(part) for part in command]


def _compose_ps_output(*entries: dict[str, object]) -> str:
    """Render Compose `ps --format json` output in the newline-delimited form."""
    return "\n".join(json.dumps(entry) for entry in entries)


def test_compose_state_dir_defaults_to_openclaw_state_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Sidecar operations should follow the configured OpenClaw state root."""
    openclaw_state_dir = tmp_path / ".openclaw"

    def _resolve_openclaw_state_dir(_repo_root: pathlib.Path) -> pathlib.Path:
        return openclaw_state_dir

    monkeypatch.delenv("STRONGCLAW_COMPOSE_STATE_DIR", raising=False)
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)

    assert _compose_state_dir(tmp_path, repo_local_state=False) == (openclaw_state_dir / "compose")


def test_compose_state_dir_explicit_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """An explicit compose-state override should take precedence."""
    override_dir = tmp_path / "override-compose-state"

    def _resolve_openclaw_state_dir(_repo_root: pathlib.Path) -> pathlib.Path:
        return tmp_path / ".openclaw"

    monkeypatch.setenv("STRONGCLAW_COMPOSE_STATE_DIR", str(override_dir))
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)

    assert _compose_state_dir(tmp_path, repo_local_state=False) == override_dir


def test_compose_state_dir_repo_local_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Repo-local compose-state commands should honor the dedicated override."""
    override_dir = tmp_path / "repo-local-override"

    monkeypatch.setenv("STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR", str(override_dir))

    assert _compose_state_dir(tmp_path, repo_local_state=True) == override_dir


def test_compose_env_exports_openclaw_and_compose_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Compose subprocesses should receive aligned state-root environment variables."""
    openclaw_state_dir = tmp_path / ".openclaw"
    config_path = tmp_path / ".openclaw" / "openclaw.json"

    def _resolve_openclaw_state_dir(
        _repo_root: pathlib.Path, *, environ: Mapping[str, str] | None = None
    ) -> pathlib.Path:
        del environ
        return openclaw_state_dir

    def _resolve_openclaw_config_path(
        _repo_root: pathlib.Path, *, environ: Mapping[str, str] | None = None
    ) -> pathlib.Path:
        del environ
        return config_path

    monkeypatch.delenv("STRONGCLAW_COMPOSE_STATE_DIR", raising=False)
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_config_path",
        _resolve_openclaw_config_path,
    )

    env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert env["OPENCLAW_STATE_DIR"] == str(openclaw_state_dir)
    assert env["STRONGCLAW_COMPOSE_STATE_DIR"] == str(openclaw_state_dir / "compose")
    assert env["OPENCLAW_CONFIG"] == str(config_path)


def test_compose_env_exports_isolated_runtime_contract(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    openclaw_state_dir = runtime_root / ".openclaw"
    config_path = openclaw_state_dir / "openclaw.json"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert env["OPENCLAW_HOME"] == str(runtime_root)
    assert env["OPENCLAW_STATE_DIR"] == str(openclaw_state_dir)
    assert env["OPENCLAW_CONFIG_PATH"] == str(config_path)
    assert env["OPENCLAW_CONFIG"] == str(config_path)
    assert env["OPENCLAW_PROFILE"] == "strongclaw-dev"
    assert env["STRONGCLAW_RUNTIME_ROOT"] == str(runtime_root)


def test_compose_env_ignores_isolated_runtime_keys_from_local_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Compose env should keep runtime isolation even if local env files define legacy paths."""
    runtime_root = tmp_path / "dev-runtime"
    local_env_file = tmp_path / "legacy.env.local"
    local_env_file.write_text(
        "\n".join(
            (
                "OPENCLAW_STATE_DIR=/tmp/legacy-openclaw-state",
                "OPENCLAW_CONFIG_PATH=/tmp/legacy-openclaw.json",
                "OPENCLAW_CONFIG=/tmp/legacy-openclaw-config.json",
                "OPENCLAW_HOME=/tmp/legacy-openclaw-home",
                "OPENCLAW_PROFILE=legacy-profile",
                "NEO4J_PASSWORD=repo-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    def _varlock_local_env_file(
        _repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> pathlib.Path:
        del home_dir, environ
        return local_env_file

    monkeypatch.setenv("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(strongclaw_ops, "varlock_local_env_file", _varlock_local_env_file)

    env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    expected_state_dir = runtime_root / ".openclaw"
    expected_config_path = expected_state_dir / "openclaw.json"
    assert env["OPENCLAW_HOME"] == str(runtime_root)
    assert env["OPENCLAW_STATE_DIR"] == str(expected_state_dir)
    assert env["OPENCLAW_CONFIG_PATH"] == str(expected_config_path)
    assert env["OPENCLAW_CONFIG"] == str(expected_config_path)
    assert env["OPENCLAW_PROFILE"] == "strongclaw-dev"
    assert env["NEO4J_PASSWORD"] == "repo-secret"


def test_compose_env_inherits_repo_local_varlock_assignments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Compose env should surface repo-local Varlock secrets for direct compose probes."""
    openclaw_state_dir = tmp_path / ".openclaw"
    config_path = openclaw_state_dir / "openclaw.json"
    local_env_file = tmp_path / "platform" / "configs" / "varlock" / ".env.local"
    local_env_file.parent.mkdir(parents=True, exist_ok=True)
    local_env_file.write_text(
        "NEO4J_PASSWORD=repo-secret\nNEO4J_USERNAME=neo4j\n", encoding="utf-8"
    )

    def _resolve_openclaw_state_dir(
        _repo_root: pathlib.Path, *, environ: Mapping[str, str] | None = None
    ) -> pathlib.Path:
        del environ
        return openclaw_state_dir

    def _resolve_openclaw_config_path(
        _repo_root: pathlib.Path, *, environ: Mapping[str, str] | None = None
    ) -> pathlib.Path:
        del environ
        return config_path

    def _varlock_local_env_file(
        _repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> pathlib.Path:
        del home_dir, environ
        return local_env_file

    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_config_path",
        _resolve_openclaw_config_path,
    )
    monkeypatch.setattr(strongclaw_ops, "varlock_local_env_file", _varlock_local_env_file)

    env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert env["NEO4J_PASSWORD"] == "repo-secret"
    assert env["NEO4J_USERNAME"] == "neo4j"


def test_compose_env_sets_project_name_for_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Compose variants should derive deterministic project names from state roots."""
    openclaw_state_dir = tmp_path / ".openclaw"
    config_path = openclaw_state_dir / "openclaw.json"
    repo_local_dir = tmp_path / "repo-local"

    def _resolve_openclaw_state_dir(
        _repo_root: pathlib.Path, *, environ: Mapping[str, str] | None = None
    ) -> pathlib.Path:
        del environ
        return openclaw_state_dir

    def _resolve_openclaw_config_path(
        _repo_root: pathlib.Path, *, environ: Mapping[str, str] | None = None
    ) -> pathlib.Path:
        del environ
        return config_path

    monkeypatch.setenv("STRONGCLAW_COMPOSE_VARIANT", "ci-hosted-macos")
    monkeypatch.setenv("STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR", str(repo_local_dir))
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_config_path",
        _resolve_openclaw_config_path,
    )

    host_env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )
    repo_env = _compose_env(
        REPO_ROOT,
        repo_local_state=True,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert re.fullmatch(r"strongclaw-sidecars-host-[0-9a-f]{10}", host_env["COMPOSE_PROJECT_NAME"])
    assert re.fullmatch(r"strongclaw-sidecars-repo-[0-9a-f]{10}", repo_env["COMPOSE_PROJECT_NAME"])
    assert host_env["COMPOSE_PROJECT_NAME"] != repo_env["COMPOSE_PROJECT_NAME"]


def test_compose_path_uses_variant_file_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Hosted macOS CI should resolve the dedicated compose variant file."""
    compose_dir = tmp_path / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    base_path = compose_dir / "docker-compose.aux-stack.yaml"
    variant_path = compose_dir / "docker-compose.aux-stack.ci-hosted-macos.yaml"
    base_path.write_text("services: {}\n", encoding="utf-8")
    variant_path.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("STRONGCLAW_COMPOSE_VARIANT", "ci-hosted-macos")

    assert _compose_path(tmp_path, "docker-compose.aux-stack.yaml") == variant_path


def test_sidecars_up_bootstraps_litellm_before_starting_runtime_services(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Sidecars startup should bootstrap LiteLLM before starting the runtime service."""
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    compose_status_payloads = iter(
        (
            _compose_ps_output({"Service": "postgres", "State": "running", "Health": "healthy"}),
            _compose_ps_output({"Service": "postgres", "State": "running", "Health": "healthy"}),
        )
    )
    inherited_calls: list[tuple[str, ...]] = []

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)

    def fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        capture_output: bool = True,
        input_text: str | None = None,
        check: bool = False,
    ) -> ExecResult:
        del cwd, env, timeout_seconds, capture_output, input_text, check
        return _exec_result(*command, stdout=next(compose_status_payloads))

    def fake_run_command_inherited(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 1800,
    ) -> int:
        del cwd, env, timeout_seconds
        inherited_calls.append(tuple(command))
        return 0

    monkeypatch.setattr(strongclaw_ops, "run_command", fake_run_command)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)

    assert strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False) == 0
    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres"),
        (
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "DISABLE_SCHEMA_UPDATE=false",
            "litellm",
            "--config",
            "/app/config.yaml",
            "--skip_server_startup",
        ),
        (
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--force-recreate",
            "litellm",
        ),
        (
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "otel-collector",
            "qdrant",
            "neo4j",
        ),
    ]


def test_sidecars_up_skips_bootstrap_when_litellm_is_already_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Healthy LiteLLM runtimes should not be re-bootstrapped on idempotent startup."""
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    compose_status_payloads = iter(
        (
            _compose_ps_output({"Service": "postgres", "State": "running", "Health": "healthy"}),
            _compose_ps_output(
                {"Service": "postgres", "State": "running", "Health": "healthy"},
                {"Service": "litellm", "State": "running", "Health": "healthy"},
            ),
        )
    )
    inherited_calls: list[tuple[str, ...]] = []

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)

    def fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        capture_output: bool = True,
        input_text: str | None = None,
        check: bool = False,
    ) -> ExecResult:
        del cwd, env, timeout_seconds, capture_output, input_text, check
        return _exec_result(*command, stdout=next(compose_status_payloads))

    def fake_run_command_inherited(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 1800,
    ) -> int:
        del cwd, env, timeout_seconds
        inherited_calls.append(tuple(command))
        return 0

    monkeypatch.setattr(strongclaw_ops, "run_command", fake_run_command)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)

    assert strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False) == 0
    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres"),
        (
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "litellm",
            "otel-collector",
            "qdrant",
            "neo4j",
        ),
    ]


def test_sidecars_up_fails_when_postgres_never_turns_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Sidecars startup should stop before LiteLLM bootstrap if Postgres is not healthy."""
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    inherited_calls: list[tuple[str, ...]] = []
    monotonic_values = iter((0.0, 2.0))

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    def _monotonic() -> float:
        return next(monotonic_values)

    def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)
    monkeypatch.setattr(strongclaw_ops, "POSTGRES_HEALTH_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(strongclaw_ops.time, "monotonic", _monotonic)
    monkeypatch.setattr(strongclaw_ops.time, "sleep", _sleep)

    def fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        capture_output: bool = True,
        input_text: str | None = None,
        check: bool = False,
    ) -> ExecResult:
        del cwd, env, timeout_seconds, capture_output, input_text, check
        return _exec_result(
            *command,
            stdout=_compose_ps_output(
                {"Service": "postgres", "State": "running", "Health": "starting"}
            ),
        )

    def fake_run_command_inherited(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 1800,
    ) -> int:
        del cwd, env, timeout_seconds
        inherited_calls.append(tuple(command))
        return 0

    monkeypatch.setattr(strongclaw_ops, "run_command", fake_run_command)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)

    with pytest.raises(
        strongclaw_ops.CommandError, match="timed out waiting for compose service 'postgres'"
    ):
        strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False)

    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres")
    ]
