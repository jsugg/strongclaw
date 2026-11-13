"""Tests for StrongClaw operational compose-state wiring."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

import pytest

from clawops import strongclaw_ops
from clawops.qdrant_storage import StorageDecision
from clawops.strongclaw_runtime import CommandError, DockerBackendLostError, DockerBackendProbe
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.clock import FakeClock
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
_adopt_legacy_compose_state = cast(
    Callable[[pathlib.Path, pathlib.Path], pathlib.Path],
    cast(Any, strongclaw_ops)._adopt_legacy_compose_state,
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
    *,
    env_mode: str = "auto",
) -> list[str]:
    """Return the raw command for tests that do not need Varlock wrapping."""
    del env_mode
    return [str(part) for part in command]


def test_compose_state_dir_follows_the_openclaw_state_root_from_outside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Each OpenClaw state root gets its own compose state, outside what openclaw backs up."""
    first = tmp_path / ".openclaw"
    second = tmp_path / ".openclaw-dev"
    roots = iter((first, second, first))

    def _resolve_openclaw_state_dir(_repo_root: pathlib.Path) -> pathlib.Path:
        return next(roots)

    monkeypatch.delenv("STRONGCLAW_COMPOSE_STATE_DIR", raising=False)
    monkeypatch.setenv("STRONGCLAW_STATE_DIR", str(tmp_path / "strongclaw-state"))
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)

    first_dir = _compose_state_dir(tmp_path, repo_local_state=False)
    second_dir = _compose_state_dir(tmp_path, repo_local_state=False)

    assert first_dir.parent == tmp_path / "strongclaw-state" / "compose"
    assert first_dir.name.startswith(".openclaw-") and first_dir != second_dir
    assert not first_dir.is_relative_to(first)
    assert _compose_state_dir(tmp_path, repo_local_state=False) == first_dir


def test_legacy_compose_state_moves_out_of_the_openclaw_state_root(
    tmp_path: pathlib.Path,
) -> None:
    legacy = tmp_path / ".openclaw" / "compose"
    (legacy / "postgres").mkdir(parents=True)
    (legacy / "postgres" / "PG_VERSION").write_text("16", encoding="utf-8")
    target = tmp_path / "state" / "compose" / "scope"

    adopted = _adopt_legacy_compose_state(legacy, target)

    assert adopted == target
    assert (target / "postgres" / "PG_VERSION").read_text(encoding="utf-8") == "16"
    assert not legacy.exists()


def test_legacy_compose_state_is_left_alone_once_the_new_location_exists(
    tmp_path: pathlib.Path,
) -> None:
    legacy = tmp_path / ".openclaw" / "compose"
    legacy.mkdir(parents=True)
    target = tmp_path / "state" / "compose" / "scope"
    target.mkdir(parents=True)

    assert _adopt_legacy_compose_state(legacy, target) == target
    assert legacy.is_dir()


def test_legacy_compose_state_stays_in_use_when_it_cannot_move(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / ".openclaw" / "compose"
    legacy.mkdir(parents=True)
    target = tmp_path / "state" / "compose" / "scope"

    def _cross_device(_self: pathlib.Path, _target: pathlib.Path) -> pathlib.Path:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(pathlib.Path, "rename", _cross_device)

    assert _adopt_legacy_compose_state(legacy, target) == legacy


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


def _stub_qdrant_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep sidecar orchestration tests focused; the qdrant upgrade has its own suite."""

    def prepare(_execution: object) -> StorageDecision:
        return StorageDecision("none", "stubbed", "1.20", "1.20")

    def complete(_execution: object) -> str:
        return "not-needed"

    monkeypatch.setattr(strongclaw_ops, "_prepare_qdrant_storage", prepare)
    monkeypatch.setattr(strongclaw_ops, "_complete_qdrant_reindex", complete)


def test_compose_execution_returns_direct_compose_command() -> None:
    """Compose execution should emit deterministic docker compose commands."""
    execution = cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={},
    )

    command = execution.command("up", "-d", "postgres")

    assert command == ["docker", "compose", "-f", "/tmp/compose.yaml", "up", "-d", "postgres"]


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
    monkeypatch.setenv("STRONGCLAW_STATE_DIR", str(tmp_path / "strongclaw-state"))
    monkeypatch.setattr(strongclaw_ops, "resolve_openclaw_state_dir", _resolve_openclaw_state_dir)
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_openclaw_config_path",
        _resolve_openclaw_config_path,
    )
    # An install from before the move keeps its sidecar data inside the OpenClaw state root.
    (openclaw_state_dir / "compose" / "qdrant").mkdir(parents=True)

    env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    compose_state_dir = pathlib.Path(env["STRONGCLAW_COMPOSE_STATE_DIR"])
    assert env["OPENCLAW_STATE_DIR"] == str(openclaw_state_dir)
    assert compose_state_dir.parent == tmp_path / "strongclaw-state" / "compose"
    assert (compose_state_dir / "qdrant").is_dir()
    assert not (openclaw_state_dir / "compose").exists()
    assert env["OPENCLAW_CONFIG"] == str(config_path)


def test_compose_env_reads_managed_varlock_env_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Compose env must request managed varlock env-mode to avoid legacy fallback drift."""
    observed: dict[str, str] = {}
    local_env_file = tmp_path / "managed.env.local"
    local_env_file.write_text("NEO4J_PASSWORD=managed-secret\n", encoding="utf-8")

    def _varlock_local_env_file(
        _repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None = None,
        environ: Mapping[str, str] | None = None,
        env_mode: str = "auto",
    ) -> pathlib.Path:
        del home_dir, environ
        observed["env_mode"] = env_mode
        return local_env_file

    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    monkeypatch.setattr(strongclaw_ops, "varlock_local_env_file", _varlock_local_env_file)

    env = _compose_env(
        REPO_ROOT,
        repo_local_state=False,
        compose_name="docker-compose.aux-stack.yaml",
    )

    assert env["NEO4J_PASSWORD"] == "managed-secret"
    assert observed["env_mode"] == strongclaw_ops.VARLOCK_ENV_MODE_MANAGED


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
        env_mode: str = "auto",
    ) -> pathlib.Path:
        del home_dir, environ, env_mode
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
        env_mode: str = "auto",
    ) -> pathlib.Path:
        del home_dir, environ, env_mode
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


def test_sidecar_readiness_targets_extend_litellm_timeout_for_hosted_macos() -> None:
    """Hosted macOS variant should give LiteLLM extra time to pass health checks."""
    profile_flags = {"usesQmd": True, "usesHypermemory": True}

    targets = cast(Any, strongclaw_ops)._sidecar_readiness_targets(
        profile_flags,
        environ={"STRONGCLAW_COMPOSE_VARIANT": "ci-hosted-macos"},
    )

    litellm_target = next(target for target in targets if target.service_name == "litellm")
    assert (
        litellm_target.timeout_seconds == strongclaw_ops.HOSTED_MACOS_LITELLM_HEALTH_TIMEOUT_SECONDS
    )


def test_sidecar_readiness_targets_keep_default_litellm_timeout_without_hosted_variant() -> None:
    """Non-hosted variants should keep the default LiteLLM readiness timeout."""
    profile_flags = {"usesQmd": True, "usesHypermemory": True}

    targets = cast(Any, strongclaw_ops)._sidecar_readiness_targets(
        profile_flags,
        environ={},
    )

    litellm_target = next(target for target in targets if target.service_name == "litellm")
    assert litellm_target.timeout_seconds == strongclaw_ops.LITELLM_HEALTH_TIMEOUT_SECONDS


def test_sidecar_readiness_targets_extend_neo4j_timeout_for_hosted_macos() -> None:
    """Hosted macOS variant should give Neo4j extra time to pass health checks."""
    profile_flags = {"usesQmd": True, "usesHypermemory": True}

    targets = cast(Any, strongclaw_ops)._sidecar_readiness_targets(
        profile_flags,
        environ={"STRONGCLAW_COMPOSE_VARIANT": "ci-hosted-macos"},
    )

    neo4j_target = next(target for target in targets if target.service_name == "neo4j")
    assert neo4j_target.timeout_seconds == strongclaw_ops.HOSTED_MACOS_NEO4J_HEALTH_TIMEOUT_SECONDS


def test_sidecar_readiness_targets_keep_default_neo4j_timeout_without_hosted_variant() -> None:
    """Non-hosted variants should keep the default Neo4j readiness timeout."""
    profile_flags = {"usesQmd": True, "usesHypermemory": True}

    targets = cast(Any, strongclaw_ops)._sidecar_readiness_targets(
        profile_flags,
        environ={},
    )

    neo4j_target = next(target for target in targets if target.service_name == "neo4j")
    assert neo4j_target.timeout_seconds == strongclaw_ops.NEO4J_HEALTH_TIMEOUT_SECONDS


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
    _stub_qdrant_storage(monkeypatch)

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
    _stub_qdrant_storage(monkeypatch)

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


def test_sidecars_up_recreates_postgres_after_initial_wait_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Sidecars startup should recover once by recreating Postgres after an initial timeout."""
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    inherited_calls: list[tuple[str, ...]] = []
    waited_services: list[str] = []
    postgres_wait_attempts = 0

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
        }

    def _wait_for_compose_service(
        _execution: object,
        *,
        service_name: str,
        state: str,
        health: str | None = None,
        timeout_seconds: int,
    ) -> None:
        del state, health, timeout_seconds
        nonlocal postgres_wait_attempts
        waited_services.append(service_name)
        if service_name == "postgres":
            postgres_wait_attempts += 1
            if postgres_wait_attempts == 1:
                raise strongclaw_ops.CommandError(
                    "timed out waiting for compose service 'postgres'"
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

    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _compose_service_statuses)
    monkeypatch.setattr(strongclaw_ops, "_wait_for_compose_service", _wait_for_compose_service)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)
    _stub_qdrant_storage(monkeypatch)

    assert strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False) == 0
    assert waited_services == ["postgres", "postgres", "postgres", "litellm", "qdrant", "neo4j"]
    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres"),
        (
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--force-recreate",
            "postgres",
        ),
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
    _stub_qdrant_storage(monkeypatch)

    with pytest.raises(
        strongclaw_ops.CommandError, match="timed out waiting for compose service 'postgres'"
    ):
        strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False)

    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres"),
        (
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--force-recreate",
            "postgres",
        ),
    ]


def test_parse_args_accepts_sidecars_up_json_flag() -> None:
    args = strongclaw_ops.parse_args(["sidecars", "up", "--json"])

    assert args.command == "sidecars"
    assert args.sidecars_command == "up"
    assert bool(args.json) is True


def test_main_sidecars_up_json_reports_required_failure_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _parse_args(_argv: list[str] | None = None) -> argparse.Namespace:
        return argparse.Namespace(
            command="sidecars",
            sidecars_command="up",
            repo_local_state=False,
            json=True,
        )

    def _resolve_asset_root_argument(
        _args: argparse.Namespace, *, command_name: str
    ) -> pathlib.Path:
        del command_name
        return REPO_ROOT

    def _sidecars_up_report(
        _repo_root: pathlib.Path,
        *,
        repo_local_state: bool,
        suppress_wait_failures: bool,
    ) -> dict[str, object]:
        del repo_local_state, suppress_wait_failures
        return {
            "ok": False,
            "readiness": {"requiredReady": False, "required": [], "optional": []},
            "command": {"exitCode": 0, "failedStep": None, "steps": []},
            "failedService": "postgres",
            "error": "timed out waiting for compose service 'postgres'",
        }

    monkeypatch.setattr(
        strongclaw_ops,
        "parse_args",
        _parse_args,
    )
    monkeypatch.setattr(
        strongclaw_ops,
        "resolve_asset_root_argument",
        _resolve_asset_root_argument,
    )
    monkeypatch.setattr(
        strongclaw_ops,
        "_sidecars_up_report",
        _sidecars_up_report,
    )

    exit_code = strongclaw_ops.main([])

    assert exit_code == 1
    payload = cast(dict[str, object], json.loads(capsys.readouterr().out.strip()))
    readiness = cast(dict[str, object], payload["readiness"])
    assert readiness["requiredReady"] is False
    assert payload["failedService"] == "postgres"
    assert "timed out waiting for compose service 'postgres'" in str(payload["error"])


def test_run_compose_command_with_context_emits_start_and_finish_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_events: list[tuple[str, dict[str, object]]] = []
    execution = cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={},
    )

    def _run_command_inherited(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = 1800,
    ) -> int:
        del command, cwd, env, timeout_seconds
        return 7

    def _emit_structured_log(event: str, payload: object) -> None:
        observed_events.append((str(event), cast(dict[str, object], payload)))

    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", _run_command_inherited)
    monkeypatch.setattr(strongclaw_ops, "emit_structured_log", _emit_structured_log)

    return_code = cast(Any, strongclaw_ops)._run_compose_command_with_context(
        execution,
        arguments=("up", "-d", "postgres"),
        timeout_seconds=120,
    )

    assert return_code == 7
    assert observed_events[0][0] == "clawops.ops.compose.exec.start"
    assert observed_events[0][1]["arguments"] == '["up","-d","postgres"]'
    assert observed_events[1][0] == "clawops.ops.compose.exec.finish"
    assert observed_events[1][1]["returncode"] == 7
    assert isinstance(observed_events[1][1]["elapsed_ms"], int)


def test_run_compose_command_with_context_emits_error_event_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_events: list[tuple[str, dict[str, object]]] = []
    execution = cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={},
    )

    def _run_command_inherited(
        command: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = 1800,
    ) -> int:
        del command, cwd, env, timeout_seconds
        raise RuntimeError("compose boom")

    def _emit_structured_log(event: str, payload: object) -> None:
        observed_events.append((str(event), cast(dict[str, object], payload)))

    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", _run_command_inherited)
    monkeypatch.setattr(strongclaw_ops, "emit_structured_log", _emit_structured_log)

    with pytest.raises(RuntimeError, match="compose boom"):
        cast(Any, strongclaw_ops)._run_compose_command_with_context(
            execution,
            arguments=("up", "-d", "postgres"),
            timeout_seconds=120,
        )

    assert observed_events[0][0] == "clawops.ops.compose.exec.start"
    assert observed_events[1][0] == "clawops.ops.compose.exec.error"
    assert observed_events[1][1]["error_type"] == "RuntimeError"
    assert "compose boom" in str(observed_events[1][1]["error"])


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


def test_wait_for_compose_service_retries_transient_status_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose status probing should tolerate transient command errors while polling."""
    execution = cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={},
    )
    attempts = {"count": 0}

    def _compose_service_statuses_flaky(_execution: object) -> dict[str, object]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise CommandError("failed to inspect sidecar services: transient compose error")
        return {
            "postgres": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                name="postgres",
                state="running",
                health="healthy",
            )
        }

    monkeypatch.setattr(
        strongclaw_ops, "_compose_service_statuses", _compose_service_statuses_flaky
    )

    def _sleep_noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(strongclaw_ops.time, "sleep", _sleep_noop)
    probes = _install_backend_probes(monkeypatch, _BACKEND_UP)

    cast(Any, strongclaw_ops)._wait_for_compose_service(
        execution,
        service_name="postgres",
        state="running",
        health="healthy",
        timeout_seconds=30,
    )

    assert attempts["count"] == 2
    assert probes.calls == 1


_BACKEND_UP = DockerBackendProbe(reachable=True)
_BACKEND_DOWN = DockerBackendProbe(
    reachable=False, detail="`docker info` failed: Cannot connect to the Docker daemon"
)
_SOCKET_MISSING = DockerBackendProbe(
    reachable=False, detail="Docker socket /tmp/orbstack/docker.sock is missing", definitive=True
)


class _BackendProbes:
    """Answer every backend probe with one fixed result, recording the probed env."""

    def __init__(self, probe: DockerBackendProbe) -> None:
        self.probe = probe
        self.calls = 0
        self.envs: list[Mapping[str, str] | None] = []

    def __call__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 15,
    ) -> DockerBackendProbe:
        del cwd, timeout_seconds
        self.calls += 1
        self.envs.append(env)
        return self.probe


def _install_backend_probes(
    monkeypatch: pytest.MonkeyPatch, probe: DockerBackendProbe
) -> _BackendProbes:
    probes = _BackendProbes(probe)
    monkeypatch.setattr(strongclaw_ops, "probe_docker_backend", probes)
    return probes


def _wait_execution() -> object:
    return cast(Any, strongclaw_ops)._ComposeExecution(
        repo_root=REPO_ROOT,
        compose_path=pathlib.Path("/tmp/compose.yaml"),
        cwd=pathlib.Path("/tmp"),
        env={"DOCKER_HOST": "unix:///tmp/orbstack/docker.sock"},
    )


def _failing_compose_statuses(_execution: object) -> dict[str, object]:
    raise CommandError("failed to inspect sidecar services: Cannot connect to the Docker daemon")


def _wait_for_litellm(timeout_seconds: int = 300) -> None:
    cast(Any, strongclaw_ops)._wait_for_compose_service(
        _wait_execution(),
        service_name="litellm",
        state="running",
        health="healthy",
        timeout_seconds=timeout_seconds,
    )


def _record_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []

    def _emit_structured_log(event: str, payload: object) -> None:
        del payload
        events.append(event)

    monkeypatch.setattr(strongclaw_ops, "emit_structured_log", _emit_structured_log)
    return events


def test_wait_for_compose_service_fails_fast_when_the_docker_socket_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(strongclaw_ops, "time", clock)
    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _failing_compose_statuses)
    probes = _install_backend_probes(monkeypatch, _SOCKET_MISSING)
    events = _record_events(monkeypatch)

    with pytest.raises(DockerBackendLostError) as exc_info:
        _wait_for_litellm()

    message = str(exc_info.value)
    assert message.startswith(
        "Docker backend became unreachable while waiting for compose service 'litellm' to "
        "reach running/healthy: Docker socket /tmp/orbstack/docker.sock is missing; "
    )
    assert "Cannot connect to the Docker daemon" in message
    assert clock.sleeps == []
    assert probes.envs == [{"DOCKER_HOST": "unix:///tmp/orbstack/docker.sock"}]
    assert events == ["clawops.ops.sidecars.wait.start", "clawops.ops.sidecars.wait.backend_lost"]


def test_wait_for_compose_service_fails_fast_after_consecutive_failed_docker_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    started = clock.monotonic()
    monkeypatch.setattr(strongclaw_ops, "time", clock)
    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _failing_compose_statuses)
    probes = _install_backend_probes(monkeypatch, _BACKEND_DOWN)

    with pytest.raises(DockerBackendLostError, match=r"\(3 failed probes in a row\)"):
        _wait_for_litellm(timeout_seconds=300)

    assert probes.calls == 3
    assert clock.sleeps == [2.0, 2.0]
    assert clock.monotonic() - started == pytest.approx(4.0)


def test_wait_for_compose_service_keeps_waiting_for_a_slow_container_on_a_healthy_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(strongclaw_ops, "time", clock)
    probes = _install_backend_probes(monkeypatch, _BACKEND_DOWN)
    polls = {"count": 0}

    def _slow_litellm(_execution: object) -> dict[str, object]:
        polls["count"] += 1
        health = "healthy" if polls["count"] > 100 else "starting"
        return {
            "litellm": cast(Any, strongclaw_ops)._ComposeServiceStatus(
                name="litellm", state="running", health=health
            )
        }

    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _slow_litellm)

    _wait_for_litellm(timeout_seconds=300)

    assert polls["count"] == 101
    assert clock.sleeps == [2.0] * 100
    assert probes.calls == 0


def test_wait_for_compose_service_still_times_out_while_the_backend_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    started = clock.monotonic()
    monkeypatch.setattr(strongclaw_ops, "time", clock)
    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _failing_compose_statuses)
    probes = _install_backend_probes(monkeypatch, _BACKEND_UP)
    events = _record_events(monkeypatch)

    with pytest.raises(CommandError) as exc_info:
        _wait_for_litellm(timeout_seconds=10)

    assert not isinstance(exc_info.value, DockerBackendLostError)
    assert str(exc_info.value).startswith(
        "timed out waiting for compose service 'litellm' to reach running/healthy; "
        "last observed service not listed in compose status; last status inspection error: "
    )
    assert clock.monotonic() - started == pytest.approx(10.0)
    assert probes.calls == 6
    assert events[-1] == "clawops.ops.sidecars.wait.timeout"


def _stub_sidecars_up_with_postgres_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    postgres_error: CommandError,
) -> tuple[pathlib.Path, list[tuple[str, ...]]]:
    """Stub sidecars orchestration so the Postgres health wait raises *postgres_error*."""
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    inherited_calls: list[tuple[str, ...]] = []

    def _compose_path_override(_repo_root: pathlib.Path, _compose_name: str) -> pathlib.Path:
        return compose_path

    def _profile_flags(_path: pathlib.Path) -> dict[str, bool | str]:
        return {"usesQmd": True, "usesHypermemory": True, "source": "test"}

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
            raise postgres_error

    def _empty_compose_statuses(_execution: object) -> dict[str, object]:
        return {}

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

    monkeypatch.setattr(strongclaw_ops, "ensure_docker_backend_ready", lambda: None)
    monkeypatch.setattr(strongclaw_ops, "_compose_path", _compose_path_override)
    monkeypatch.setattr(strongclaw_ops, "_compose_env", _fixed_compose_env)
    monkeypatch.setattr(strongclaw_ops, "_resolve_profile_dependency_flags", _profile_flags)
    monkeypatch.setattr(strongclaw_ops, "_wait_for_compose_service", _wait_for_compose_service)
    monkeypatch.setattr(strongclaw_ops, "_compose_service_statuses", _empty_compose_statuses)
    monkeypatch.setattr(strongclaw_ops, "run_command_inherited", fake_run_command_inherited)
    _stub_qdrant_storage(monkeypatch)
    return compose_path, inherited_calls


_POSTGRES_BACKEND_LOST = DockerBackendLostError(
    "Docker backend became unreachable while waiting for compose service 'postgres'"
)


def test_sidecars_up_does_not_recreate_postgres_once_the_backend_is_lost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    compose_path, inherited_calls = _stub_sidecars_up_with_postgres_wait(
        monkeypatch, tmp_path, _POSTGRES_BACKEND_LOST
    )

    with pytest.raises(DockerBackendLostError, match="Docker backend became unreachable"):
        strongclaw_ops.sidecars_up(REPO_ROOT, repo_local_state=False)

    assert inherited_calls == [
        ("docker", "compose", "-f", str(compose_path), "up", "-d", "postgres")
    ]


def test_sidecars_up_json_reports_backend_loss_during_the_postgres_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    _, inherited_calls = _stub_sidecars_up_with_postgres_wait(
        monkeypatch, tmp_path, _POSTGRES_BACKEND_LOST
    )

    payload = cast(Any, strongclaw_ops)._sidecars_up_report(
        REPO_ROOT, repo_local_state=False, suppress_wait_failures=True
    )

    assert payload["ok"] is False
    assert payload["failedService"] == "postgres"
    assert payload["error"] == str(_POSTGRES_BACKEND_LOST)
    assert payload["command"]["exitCode"] == 1
    assert len(inherited_calls) == 1


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
