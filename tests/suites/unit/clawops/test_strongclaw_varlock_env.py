"""Unit tests for StrongClaw Varlock env defaults."""

from __future__ import annotations

import pathlib

from clawops.strongclaw_runtime import (
    load_env_assignments,
    varlock_env_template_file,
)
from clawops.strongclaw_varlock_env import configure_varlock_env
from tests.plugins.infrastructure.context import TestContext


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
                "OPENCLAW_OLLAMA_MODEL=llama3:latest",
                "OPENCLAW_DEFAULT_MODEL=ollama/llama3:latest",
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
