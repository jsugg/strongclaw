"""Contracts for pytest capability marker ownership."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.fixtures.repo import REPO_ROOT


def _module_marker_names(path: Path) -> set[str]:
    """Return pytest marker names declared via module-level pytestmark."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != "pytestmark":
            continue
        return _extract_marker_names(node.value)
    return set()


def _extract_marker_names(node: ast.expr) -> set[str]:
    """Extract marker names from one supported pytestmark expression."""
    if isinstance(node, ast.Call):
        return _extract_marker_names(node.func)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
        if isinstance(node.value.value, ast.Name) and node.value.value.id == "pytest":
            if node.value.attr == "mark":
                return {node.attr}
    if isinstance(node, ast.Tuple | ast.List):
        markers: set[str] = set()
        for element in node.elts:
            markers.update(_extract_marker_names(element))
        return markers
    raise AssertionError(f"Unsupported pytestmark declaration shape: {ast.dump(node)}")


def test_root_conftest_only_assigns_structural_markers() -> None:
    source = (REPO_ROOT / "tests/conftest.py").read_text(encoding="utf-8")

    assert "_QDRANT_MARKED_FILES" not in source
    assert "_NETWORK_MARKED_FILES" not in source
    assert "pytest.mark.qdrant" not in source
    assert "pytest.mark.network_local" not in source


def test_capability_markers_are_declared_by_the_modules_that_need_them() -> None:
    expected_markers = {
        "tests/suites/unit/clawops/hypermemory/test_engine_backend_search.py": {"qdrant"},
        "tests/suites/unit/clawops/hypermemory/test_engine_backend_verify.py": {"qdrant"},
        "tests/suites/unit/clawops/hypermemory/test_qdrant_backend.py": {"qdrant"},
        "tests/suites/integration/clawops/hypermemory/test_qdrant_integration.py": {
            "qdrant",
            "network_local",
        },
        "tests/suites/integration/clawops/test_platform_verify.py": {"network_local"},
    }

    for relative_path, expected in expected_markers.items():
        module_path = REPO_ROOT / relative_path
        assert _module_marker_names(module_path) == expected
