"""Unit tests for StrongClaw Varlock env defaults."""

from __future__ import annotations

import pathlib

import pytest

from clawops.strongclaw_runtime import (
    load_env_assignments,
    varlock_env_template_file,
    varlock_local_env_file,
)
from clawops.strongclaw_varlock_env import configure_varlock_env


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = iter(
        (
            "gateway-secret-value",
            "litellm-master-secret",
            "litellm-db-secret",
            "neo4j-password-secret",
        )
    )
    monkeypatch.setattr(
        "clawops.strongclaw_varlock_env.generate_secret_value",
        lambda: next(secrets),
    )
    monkeypatch.setattr(
        "clawops.strongclaw_varlock_env._ensure_hypermemory_embedding_model",
        _no_op_model_validation,
    )
    monkeypatch.setattr(
        "clawops.strongclaw_varlock_env._validate_secret_backend_configuration",
        _no_op_secret_backend_validation,
    )
    monkeypatch.setattr(
        "clawops.strongclaw_varlock_env._validate_with_varlock",
        _varlock_validation_success,
    )

    template_path = varlock_env_template_file(tmp_path)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("APP_ENV=local\n", encoding="utf-8")

    result = configure_varlock_env(tmp_path, check_only=False, non_interactive=True)

    assert result["ok"] is True
    autofilled_values = result["autofilledValues"]
    assert isinstance(autofilled_values, int)
    assert autofilled_values > 0

    values = load_env_assignments(varlock_local_env_file(tmp_path))
    assert values["NEO4J_USERNAME"] == "neo4j"
    assert values["NEO4J_PASSWORD"] == "neo4j-password-secret"
