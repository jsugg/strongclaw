"""Core pytest configuration and core fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.repo import REPO_ROOT

TESTS_ROOT = Path(__file__).resolve().parent
_QDRANT_MARKED_FILES = {
    "test_engine_backends.py",
    "test_qdrant_backend.py",
    "test_qdrant_integration.py",
}
_NETWORK_MARKED_FILES = {
    "test_observability.py",
    "test_platform_verify.py",
    "test_qdrant_integration.py",
    "test_hypermemory_observability.py",
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply suite markers from the collected path layout."""
    del config
    for item in items:
        path = Path(str(item.fspath)).resolve()
        rel_path = path.relative_to(TESTS_ROOT)
        parts = rel_path.parts
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "contracts" in parts:
            item.add_marker(pytest.mark.contract)
        if "hypermemory" in parts:
            item.add_marker(pytest.mark.hypermemory)
        if path.name in _QDRANT_MARKED_FILES:
            item.add_marker(pytest.mark.qdrant)
        if path.name in _NETWORK_MARKED_FILES:
            item.add_marker(pytest.mark.network_local)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the repository root for tests that need stable path access."""
    return REPO_ROOT


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """Create an isolated fake home directory for tests."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    return home_dir
