"""Regression tests for shipped Varlock environment surfaces."""

from __future__ import annotations

from tests.fixtures.repo import REPO_ROOT


def test_varlock_schema_and_examples_do_not_reference_jira() -> None:
    files = (
        REPO_ROOT / "platform/configs/varlock/.env.schema",
        REPO_ROOT / "platform/configs/varlock/.env.local.example",
        REPO_ROOT / "platform/configs/varlock/.env.prod.example",
    )

    for path in files:
        assert "JIRA_" not in path.read_text(encoding="utf-8")


def test_varlock_schema_and_examples_use_varlock_compatible_home_paths() -> None:
    files = (
        REPO_ROOT / "platform/configs/varlock/.env.schema",
        REPO_ROOT / "platform/configs/varlock/.env.local.example",
        REPO_ROOT / "platform/configs/varlock/.env.prod.example",
    )

    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "$HOME/.openclaw" not in text


def test_varlock_schema_and_examples_surface_hypermemory_hypermemory_keys() -> None:
    files = (
        REPO_ROOT / "platform/configs/varlock/.env.schema",
        REPO_ROOT / "platform/configs/varlock/.env.local.example",
        REPO_ROOT / "platform/configs/varlock/.env.prod.example",
        REPO_ROOT / "platform/configs/varlock/.env.ci.example",
    )

    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "HYPERMEMORY_EMBEDDING_MODEL" in text
        assert "HYPERMEMORY_EMBEDDING_API_BASE" in text
        assert "HYPERMEMORY_EMBEDDING_BASE_URL" in text
        assert "HYPERMEMORY_QDRANT_URL" in text
