"""Contracts for testing-framework documentation surfaces."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def test_testing_framework_doc_exists() -> None:
    assert (REPO_ROOT / "platform" / "docs" / "TESTING_FRAMEWORK.md").is_file()


def test_testing_operations_doc_exists() -> None:
    assert (REPO_ROOT / "platform" / "docs" / "TESTING_OPERATIONS.md").is_file()


def test_fixture_readme_exists() -> None:
    assert (REPO_ROOT / "tests" / "fixtures" / "README.md").is_file()


def test_docs_mention_lane_model() -> None:
    framework_doc = (REPO_ROOT / "platform" / "docs" / "TESTING_FRAMEWORK.md").read_text(
        encoding="utf-8"
    )

    for lane in ("unit", "integration", "contracts"):
        assert lane in framework_doc
