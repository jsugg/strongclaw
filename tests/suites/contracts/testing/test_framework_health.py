"""Framework health checks for test infrastructure drift."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib

from tests.utils.helpers.repo import REPO_ROOT
from tests.utils.scripts.analyze_fixtures import analyze_fixture_tree

_EXPECTED_MARKERS = {
    "contract",
    "e2e",
    "framework",
    "hypermemory",
    "integration",
    "network_local",
    "qdrant",
    "test_profile",
    "unit",
}


def test_root_conftest_stays_lean() -> None:
    lines = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 80


def test_no_helper_module_exceeds_250_lines() -> None:
    for path in sorted((REPO_ROOT / "tests" / "utils" / "helpers").glob("*.py")):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 250, f"{path.relative_to(REPO_ROOT)} has {line_count} lines"


def test_no_infrastructure_module_exceeds_250_lines() -> None:
    for path in sorted((REPO_ROOT / "tests" / "plugins" / "infrastructure").glob("*.py")):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= 250, f"{path.relative_to(REPO_ROOT)} has {line_count} lines"


def test_all_fixture_modules_have_docstrings() -> None:
    for path in sorted((REPO_ROOT / "tests" / "fixtures").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        assert ast.get_docstring(
            tree
        ), f"{path.relative_to(REPO_ROOT)} is missing a module docstring"


def test_all_infrastructure_modules_have_docstrings() -> None:
    for path in sorted((REPO_ROOT / "tests" / "plugins" / "infrastructure").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        assert ast.get_docstring(
            tree
        ), f"{path.relative_to(REPO_ROOT)} is missing a module docstring"


def test_tests_do_not_import_fixture_modules() -> None:
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        if "fixtures" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("tests.fixtures"):
                    raise AssertionError(
                        f"{path.relative_to(REPO_ROOT)} imports {node.module}; "
                        "import helpers from tests.utils.helpers or use fixture injection."
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("tests.fixtures"):
                        raise AssertionError(
                            f"{path.relative_to(REPO_ROOT)} imports {alias.name}; "
                            "import helpers from tests.utils.helpers or use fixture injection."
                        )


def test_fixture_analysis_runs_successfully() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tests.utils.scripts.analyze_fixtures", "--json"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["total_fixtures"] >= 1


def test_no_duplicate_fixture_definitions() -> None:
    report = analyze_fixture_tree(REPO_ROOT)
    assert report["duplicates"] == []


def test_marker_inventory_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = {
        entry.split(":", maxsplit=1)[0]
        for entry in pyproject["tool"]["pytest"]["ini_options"]["markers"]
    }
    assert markers == _EXPECTED_MARKERS


def test_no_top_level_test_modules_outside_suites() -> None:
    top_level_tests = sorted((REPO_ROOT / "tests").glob("test_*.py"))
    assert top_level_tests == []
