"""Contracts for testing-framework documentation surfaces."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT

DOCS_ROOT = REPO_ROOT / "docs" / "testing"


def test_testing_docs_entrypoint_exists() -> None:
    assert (DOCS_ROOT / "README.md").is_file()


def test_testing_authoring_doc_exists() -> None:
    assert (DOCS_ROOT / "authoring.md").is_file()


def test_testing_operations_doc_exists() -> None:
    assert (DOCS_ROOT / "operations.md").is_file()


def test_fixture_readme_exists() -> None:
    assert (REPO_ROOT / "tests" / "fixtures" / "README.md").is_file()


def test_docs_entrypoint_links_to_authoring_and_operations() -> None:
    readme = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")

    assert "authoring.md" in readme
    assert "operations.md" in readme
    assert "tests/fixtures/README.md" in readme


def test_authoring_doc_mentions_lane_model() -> None:
    authoring_doc = (DOCS_ROOT / "authoring.md").read_text(encoding="utf-8")

    for lane in ("unit", "integration", "contracts", "framework"):
        assert lane in authoring_doc


def test_docs_mention_infrastructure_runtime_namespace() -> None:
    authoring_doc = (DOCS_ROOT / "authoring.md").read_text(encoding="utf-8")
    fixture_readme = (REPO_ROOT / "tests" / "fixtures" / "README.md").read_text(encoding="utf-8")

    assert "tests/plugins/infrastructure" in authoring_doc
    assert "docs/testing/authoring.md" in fixture_readme
    assert "tests/plugins/infrastructure" in fixture_readme
