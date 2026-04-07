"""Unit tests for StrongClaw Varlock env defaults."""

from __future__ import annotations

import pathlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from clawops import strongclaw_varlock_env
from clawops.strongclaw_runtime import (
    ExecResult,
    load_env_assignments,
    varlock_env_template_file,
)
from clawops.strongclaw_varlock_env import configure_varlock_env
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.assets import make_asset_root


def _no_op_model_validation(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    non_interactive: bool,
) -> None:
    del repo_root, check_only, non_interactive


def _no_op_secret_backend_validation(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
    non_interactive: bool,
) -> None:
    del repo_root, check_only, non_interactive


def _varlock_validation_success(
    repo_root: pathlib.Path,
    *,
    check_only: bool,
) -> bool:
    del repo_root, check_only
    return True


def test_ensure_required_defaults_generates_neo4j_credentials(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    local_env_file = tmp_path / ".env.local"
    test_context.env.set("VARLOCK_LOCAL_ENV_FILE", str(local_env_file))
    secrets = iter(
        (
            "gateway-secret-value",
            "litellm-master-secret",
            "litellm-db-secret",
            "neo4j-password-secret",
        )
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env.generate_secret_value",
        new=lambda: next(secrets),
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._ensure_hypermemory_embedding_model",
        new=_no_op_model_validation,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_secret_backend_configuration",
        new=_no_op_secret_backend_validation,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_with_varlock",
        new=_varlock_validation_success,
    )

    template_path = varlock_env_template_file(tmp_path)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("APP_ENV=local\n", encoding="utf-8")

    result = configure_varlock_env(tmp_path, check_only=False, non_interactive=True)

    assert result["ok"] is True
    autofilled_values = result["autofilledValues"]
    assert isinstance(autofilled_values, int)
    assert autofilled_values > 0

    values = load_env_assignments(local_env_file)
    assert values["NEO4J_USERNAME"] == "neo4j"
    assert values["NEO4J_PASSWORD"] == "neo4j-password-secret"
    assert values["HYPERMEMORY_EMBEDDING_MODEL"] == "ollama/nomic-embed-text"
    assert values["HYPERMEMORY_EMBEDDING_API_BASE"] == "http://host.docker.internal:11434"


def test_configure_varlock_env_replaces_short_local_secrets(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    local_env_file = tmp_path / ".env.local"
    test_context.env.set("VARLOCK_LOCAL_ENV_FILE", str(local_env_file))
    secrets = iter(
        (
            "gateway-secret-value-that-is-long-enough-for-validation",
            "litellm-master-secret-value",
            "litellm-db-secret-value",
            "neo4j-password-secret-value",
        )
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env.generate_secret_value",
        new=lambda: next(secrets),
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._ensure_hypermemory_embedding_model",
        new=_no_op_model_validation,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_secret_backend_configuration",
        new=_no_op_secret_backend_validation,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_with_varlock",
        new=_varlock_validation_success,
    )

    template_path = varlock_env_template_file(tmp_path)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("APP_ENV=local\n", encoding="utf-8")
    local_env_file.write_text(
        "\n".join(
            (
                "APP_ENV=local",
                "OPENCLAW_GATEWAY_TOKEN=short-token",
                "NEO4J_PASSWORD=short-password",
                "LITELLM_MASTER_KEY=short-master-key",
                "LITELLM_DB_PASSWORD=short-db",
                "OLLAMA_API_KEY=ollama-local",
                "OPENCLAW_OLLAMA_MODEL=deepseek-r1:latest",
                "OPENCLAW_DEFAULT_MODEL=ollama/deepseek-r1:latest",
                "HYPERMEMORY_EMBEDDING_MODEL=ollama/nomic-embed-text",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = configure_varlock_env(tmp_path, check_only=False, non_interactive=True)

    assert result["ok"] is True
    values = load_env_assignments(local_env_file)
    assert values["OPENCLAW_GATEWAY_TOKEN"] == (
        "gateway-secret-value-that-is-long-enough-for-validation"
    )
    assert values["NEO4J_PASSWORD"] == "neo4j-password-secret-value"
    assert values["LITELLM_MASTER_KEY"] == "litellm-master-secret-value"
    assert values["LITELLM_DB_PASSWORD"] == "litellm-db-secret-value"


def test_configure_varlock_env_non_interactive_autofills_local_ollama_model_chain(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    local_env_file = tmp_path / ".env.local"
    test_context.env.set("VARLOCK_LOCAL_ENV_FILE", str(local_env_file))
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_secret_backend_configuration",
        new=_no_op_secret_backend_validation,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_with_varlock",
        new=_varlock_validation_success,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env.resolve_profile",
        new=lambda: "hypermemory",
    )

    def _run_command(command: Sequence[str], **_kwargs: object) -> ExecResult:
        argv = tuple(command)
        if tuple(command[:2]) == ("ollama", "list"):
            return ExecResult(
                argv=argv,
                returncode=0,
                stdout=(
                    "NAME ID SIZE MODIFIED\n"
                    "deepseek-r1:latest abc 4.7 GB 14 months ago\n"
                    "nomic-embed-text:latest def 274 MB 20 months ago\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if tuple(command[:2]) == ("ollama", "show"):
            return ExecResult(
                argv=argv,
                returncode=0,
                stdout="Model\n  context length 32768\n",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected command: {command!r}")

    test_context.patch.patch("clawops.strongclaw_varlock_env.run_command", new=_run_command)

    template_path = varlock_env_template_file(tmp_path)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("APP_ENV=local\n", encoding="utf-8")

    result = configure_varlock_env(tmp_path, check_only=False, non_interactive=True)

    assert result["ok"] is True
    values = load_env_assignments(local_env_file)
    assert values["OLLAMA_API_KEY"] == "ollama-local"
    assert values["OPENCLAW_OLLAMA_MODEL"] == "deepseek-r1:latest"
    assert values["OPENCLAW_DEFAULT_MODEL"] == "ollama/deepseek-r1:latest"
    assert values["HYPERMEMORY_EMBEDDING_MODEL"] == "ollama/nomic-embed-text"
    assert values["HYPERMEMORY_EMBEDDING_BASE_URL"] == "http://127.0.0.1:4000/v1"


def test_configure_varlock_env_non_interactive_uses_runtime_embedding_model(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    local_env_file = tmp_path / ".env.local"
    test_context.env.set("VARLOCK_LOCAL_ENV_FILE", str(local_env_file))
    test_context.env.set("HYPERMEMORY_EMBEDDING_MODEL", "openai/text-embedding-3-small")
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_secret_backend_configuration",
        new=_no_op_secret_backend_validation,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env._validate_with_varlock",
        new=_varlock_validation_success,
    )
    test_context.patch.patch(
        "clawops.strongclaw_varlock_env.resolve_profile",
        new=lambda: "hypermemory",
    )

    def _run_command(command: Sequence[str], **_kwargs: object) -> ExecResult:
        argv = tuple(command)
        if tuple(command[:2]) == ("ollama", "list"):
            return ExecResult(
                argv=argv,
                returncode=0,
                stdout=(
                    "NAME ID SIZE MODIFIED\n"
                    "deepseek-r1:latest abc 4.7 GB 14 months ago\n"
                    "nomic-embed-text:latest def 274 MB 20 months ago\n"
                ),
                stderr="",
                duration_ms=1,
            )
        if tuple(command[:2]) == ("ollama", "show"):
            return ExecResult(
                argv=argv,
                returncode=0,
                stdout="Model\n  context length 32768\n",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected command: {command!r}")

    test_context.patch.patch(
        "clawops.strongclaw_varlock_env.run_command",
        new=_run_command,
    )

    template_path = varlock_env_template_file(tmp_path)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("APP_ENV=local\n", encoding="utf-8")

    result = configure_varlock_env(tmp_path, check_only=False, non_interactive=True)

    assert result["ok"] is True
    values = load_env_assignments(local_env_file)
    assert values["HYPERMEMORY_EMBEDDING_MODEL"] == "openai/text-embedding-3-small"


def test_varlock_env_main_honors_env_mode_wrapper(
    test_context: TestContext,
    tmp_path: pathlib.Path,
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
    requested_modes: list[str] = []
    configure_calls: list[tuple[bool, bool]] = []

    @contextmanager
    def _use_varlock_env_mode(env_mode: str) -> Iterator[None]:
        requested_modes.append(env_mode)
        yield

    def _configure_varlock_env(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        non_interactive: bool,
    ) -> dict[str, object]:
        assert repo_root == asset_root
        configure_calls.append((check_only, non_interactive))
        return {"ok": True}

    test_context.patch.patch_object(
        strongclaw_varlock_env,
        "use_varlock_env_mode",
        new=_use_varlock_env_mode,
    )
    test_context.patch.patch_object(
        strongclaw_varlock_env,
        "configure_varlock_env",
        new=_configure_varlock_env,
    )

    exit_code = strongclaw_varlock_env.main(
        ["--asset-root", str(asset_root), "--env-mode", "legacy", "check"]
    )

    assert exit_code == 0
    assert requested_modes == ["legacy"]
    assert configure_calls == [(True, False)]
