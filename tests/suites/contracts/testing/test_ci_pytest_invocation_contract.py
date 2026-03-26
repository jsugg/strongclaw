"""Contracts for CI pytest invocation policy."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT

_WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _workflow_lines() -> list[tuple[str, int, str]]:
    lines: list[tuple[str, int, str]] = []
    for workflow_path in sorted(_WORKFLOWS.glob("*.yml")):
        for line_number, line in enumerate(
            workflow_path.read_text(encoding="utf-8").splitlines(), 1
        ):
            lines.append((workflow_path.name, line_number, line))
    return lines


def test_no_pythonpath_src_in_workflows() -> None:
    for name, line_number, line in _workflow_lines():
        if "PYTHONPATH=src" in line and "pytest" in line:
            raise AssertionError(
                f"{name}:{line_number}: pytest must rely on pyproject pythonpath, not PYTHONPATH=src."
            )


def test_no_direct_test_file_references_in_workflows() -> None:
    for name, line_number, line in _workflow_lines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "pytest" in stripped and "test_" in stripped and ".py" in stripped:
            raise AssertionError(
                f"{name}:{line_number}: direct test file reference found; use suites or markers."
            )
