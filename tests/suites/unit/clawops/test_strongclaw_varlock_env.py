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
