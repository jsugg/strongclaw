"""Core pytest bootstrap and shared path fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.utils.helpers.repo import REPO_ROOT

pytest_plugins = (
    "tests.plugins.infrastructure",
    "tests.fixtures",
)

TESTS_ROOT = Path(__file__).resolve().parent
_DEVFLOW_MARKED_FILES = {
    "suites/contracts/repo/test_devflow_ci_contract.py",
    "suites/contracts/repo/test_devflow_docs_contract.py",
    "suites/contracts/repo/test_devflow_role_assets.py",
    "suites/e2e/ci/test_devflow_public_cli.py",
    "suites/integration/clawops/test_devflow_plan_run.py",
    "suites/integration/clawops/test_devflow_recovery.py",
    "suites/integration/clawops/test_devflow_sample_repos.py",
    "suites/integration/clawops/test_workflow_runner_devflow.py",
    "suites/integration/clawops/test_workflow_runner_resume.py",
    "suites/integration/clawops/test_workspace_bootstrap_profiles.py",
    "suites/unit/clawops/test_devflow_artifacts.py",
    "suites/unit/clawops/test_devflow_cli.py",
    "suites/unit/clawops/test_devflow_roles.py",
    "suites/unit/clawops/test_devflow_state.py",
    "suites/unit/clawops/test_devflow_workspaces.py",
    "suites/unit/clawops/test_git_gates.py",
    "suites/unit/clawops/test_workspace_bootstrap.py",
}
_DEVFLOW_MARKED_TESTS = {
    "suites/contracts/repo/test_ci_workflow_surfaces.py::test_devflow_contract_workflow_surfaces_public_devflow_lane",
    "suites/unit/clawops/test_cli.py::test_root_help_is_available",
    "suites/unit/clawops/test_op_journal.py::test_devflow_tables_are_created_and_stuck_runs_are_queryable",
    "suites/unit/clawops/test_orchestration.py::test_orchestration_task_resolution_includes_context_and_artifacts",
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply suite markers from the collected path layout."""
    del config
    for item in items:
        path = Path(str(item.fspath)).resolve()
        rel_path = path.relative_to(TESTS_ROOT)
        rel_path_posix = rel_path.as_posix()
        parts = rel_path.parts
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "contracts" in parts:
            item.add_marker(pytest.mark.contract)
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
        if "framework" in parts:
            item.add_marker(pytest.mark.framework)
        if "hypermemory" in parts:
            item.add_marker(pytest.mark.hypermemory)
        if rel_path_posix in _DEVFLOW_MARKED_FILES:
            item.add_marker(pytest.mark.devflow)
        if f"{rel_path_posix}::{item.name}" in _DEVFLOW_MARKED_TESTS:
            item.add_marker(pytest.mark.devflow)


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
