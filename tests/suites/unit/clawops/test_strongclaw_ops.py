"""Tests for StrongClaw operational compose-state wiring."""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

import pytest

from clawops import strongclaw_ops
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


def _fixed_compose_env(
    _repo_root: pathlib.Path,
    *,
    repo_local_state: bool,
    compose_name: str,
) -> dict[str, str]:
    """Return a minimal compose environment for command-sequencing tests."""
    del repo_local_state, compose_name
    return {
        "PATH": "/usr/bin",
        "OPENCLAW_CONFIG": "/tmp/openclaw.json",
        "STRONGCLAW_COMPOSE_STATE_DIR": "/tmp/compose",
    }


def _identity_varlock_command(
    _repo_root: pathlib.Path,
    command: Sequence[str],
) -> list[str]:
    """Return the raw command for tests that do not need Varlock wrapping."""
    return [str(part) for part in command]


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
    inherited_calls: list[tuple[str, ...]] = []
    waited_services: list[tuple[str, str, str | None, int]] = []

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)

    def _profile_flags(_path: pathlib.Path) -> dict[str, bool | str]:
        return {
            "usesQmd": True,
            "usesHypermemory": True,
            "source": "test",
        }

    monkeypatch.setattr(
        strongclaw_ops,
        "_resolve_profile_dependency_flags",
        _profile_flags,
    )

    compose_statuses = iter(
        (
            {
                "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="postgres",
                    state="running",
                    health="healthy",
                )
            },
            {
                "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="postgres",
                    state="running",
                    health="healthy",
                ),
                "litellm": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="litellm",
                    state="running",
                    health="healthy",
                ),
                "qdrant": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="qdrant",
                    state="running",
                    health="healthy",
                ),
                "neo4j": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="neo4j",
                    state="running",
                    health="healthy",
                ),
                "otel-collector": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="otel-collector",
                    state="running",
                    health=None,
                ),
            },
        )
    )

    def _compose_service_statuses(_execution: object) -> dict[str, object]:
        return cast(dict[str, object], next(compose_statuses))

    def _wait_for_compose_service(
        _execution: object,
        *,
        service_name: str,
        state: str,
        health: str | None = None,
        timeout_seconds: int,
    ) -> None:
        waited_services.append((service_name, state, health, timeout_seconds))

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

    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _compose_service_statuses)
    monkeypatch.setattr(strongclaw_ops, "_wait_for_compose_service", _wait_for_compose_service)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)

    assert strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False) == 0
    assert [service for service, _, _, _ in waited_services] == [
        "postgres",
        "postgres",
        "litellm",
        "qdrant",
        "neo4j",
    ]
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
    inherited_calls: list[tuple[str, ...]] = []
    waited_services: list[str] = []

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)

    def _profile_flags(_path: pathlib.Path) -> dict[str, bool | str]:
        return {
            "usesQmd": True,
            "usesHypermemory": True,
            "source": "test",
        }

    monkeypatch.setattr(
        strongclaw_ops,
        "_resolve_profile_dependency_flags",
        _profile_flags,
    )

    compose_statuses = iter(
        (
            {
                "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="postgres",
                    state="running",
                    health="healthy",
                ),
                "litellm": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="litellm",
                    state="running",
                    health="healthy",
                ),
            },
            {
                "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="postgres",
                    state="running",
                    health="healthy",
                ),
                "litellm": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="litellm",
                    state="running",
                    health="healthy",
                ),
                "qdrant": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="qdrant",
                    state="running",
                    health="healthy",
                ),
                "neo4j": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="neo4j",
                    state="running",
                    health="healthy",
                ),
                "otel-collector": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                    name="otel-collector",
                    state="running",
                    health=None,
                ),
            },
        )
    )

    def _compose_service_statuses(_execution: object) -> dict[str, object]:
        return cast(dict[str, object], next(compose_statuses))

    def _wait_for_compose_service(
        _execution: object,
        *,
        service_name: str,
        state: str,
        health: str | None = None,
        timeout_seconds: int,
    ) -> None:
        del state, health, timeout_seconds
        waited_services.append(service_name)

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

    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _compose_service_statuses)
    monkeypatch.setattr(strongclaw_ops, "_wait_for_compose_service", _wait_for_compose_service)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)

    assert strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False) == 0
    assert waited_services == ["postgres", "postgres", "litellm", "qdrant", "neo4j"]
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

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)

    def _profile_flags(_path: pathlib.Path) -> dict[str, bool | str]:
        return {
            "usesQmd": True,
            "usesHypermemory": True,
            "source": "test",
        }

    monkeypatch.setattr(
        strongclaw_ops,
        "_resolve_profile_dependency_flags",
        _profile_flags,
    )

    def _wait_for_compose_service(
        _execution: object,
        *,
        service_name: str,
        state: str,
        health: str | None = None,
        timeout_seconds: int,
    ) -> None:
        del state, health, timeout_seconds
        if service_name == "postgres":
            raise strongclaw_ops.CommandError("timed out waiting for compose service 'postgres'")

    monkeypatch.setattr(strongclaw_ops, "_wait_for_compose_service", _wait_for_compose_service)

    def _empty_compose_statuses(_execution: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _empty_compose_statuses)

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

    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)

    with pytest.raises(
        strongclaw_ops.CommandError, match="timed out waiting for compose service 'postgres'"
    ):
        strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False)

    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres")
    ]


def test_wait_for_compose_service_emits_start_and_ready_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_events: list[tuple[str, dict[str, object]]] = []
    execution = cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={},
    )

    def _compose_service_statuses_ready(_execution: object) -> dict[str, object]:
        return {
            "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                name="postgres",
                state="running",
                health="healthy",
            )
        }

    def _emit_structured_log(event: str, payload: object) -> None:
        observed_events.append((str(event), cast(dict[str, object], payload)))

    monkeypatch.setattr(
        strongclaw_ops, "_compose_service_statuses", _compose_service_statuses_ready
    )
    monkeypatch.setattr(strongclaw_ops, "emit_structured_log", _emit_structured_log)

    cast(Any, strongclaw_ops)._wait_for_compose_service(
        execution,
        service_name="postgres",
        state="running",
        health="healthy",
        timeout_seconds=30,
    )

    assert observed_events[0][0] == "clawops.ops.sidecars.wait.start"
    assert observed_events[0][1]["service"] == "postgres"
    assert observed_events[1][0] == "clawops.ops.sidecars.wait.ready"
    assert observed_events[1][1]["service"] == "postgres"


def test_gateway_start_uses_unbounded_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(strongclaw_ops, "wrap_command_with_varlock", _identity_varlock_command)

    def _run_command_inherited(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = 1800,
    ) -> int:
        del env
        captured["command"] = command
        captured["cwd"] = cwd
        captured["timeout_seconds"] = timeout_seconds
        return 0

    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", _run_command_inherited)

    assert strongclaw_ops.gateway_start(REPO_ROOT) == 0
    assert captured["command"] == ["openclaw", "gateway"]
    assert captured["cwd"] == REPO_ROOT
    assert captured["timeout_seconds"] is None


def test_status_returns_structured_readiness_with_impact_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={
            "OPENCLAW_CONFIG": "/tmp/openclaw.json",
            "STRONGCLAW_COMPOSE_STATE_DIR": "/tmp/compose",
        },
    )

    def _compose_execution_stub(*_args: object, **_kwargs: object) -> object:
        return execution

    def _profile_flags(_path: pathlib.Path) -> dict[str, bool | str]:
        return {
            "usesQmd": False,
            "usesHypermemory": False,
            "source": "test",
        }

    def _compose_service_statuses(_execution: object) -> dict[str, object]:
        return {
            "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                name="postgres",
                state="running",
                health="healthy",
            ),
            "litellm": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                name="litellm",
                state="running",
                health="healthy",
            ),
        }

    monkeypatch.setattr(strongclaw_ops, "_compose_execution", _compose_execution_stub)
    monkeypatch.setattr(strongclaw_ops, "_resolve_profile_dependency_flags", _profile_flags)
    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _compose_service_statuses)

    payload = strongclaw_ops.status(REPO_ROOT, repo_local_state=False)

    assert payload["ok"] is True
    readiness = cast(dict[str, object], payload["readiness"])
    assert readiness["requiredReady"] is True
    optional = cast(list[dict[str, object]], readiness["optional"])
    qdrant = next(entry for entry in optional if entry["service"] == "qdrant")
    assert qdrant["impact"] == "degraded"
    assert qdrant["ready"] is False
