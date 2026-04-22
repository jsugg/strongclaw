"""Contracts for explicit fixture typing in suite test functions."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tests.utils.helpers.repo import REPO_ROOT

_SUITES_ROOT = REPO_ROOT / "tests" / "suites"


@dataclass(frozen=True, slots=True)
class _Violation:
    """One suite-test typing violation discovered by AST inspection."""

    path: Path
    line: int
    detail: str


def _iter_suite_test_functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            yield node


def _iter_param_violations(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[_Violation]:
    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        if arg.arg in {"self", "cls"}:
            continue
        if arg.annotation is None:
            yield _Violation(path, node.lineno, f"missing annotation for '{arg.arg}'")
            continue
        if isinstance(arg.annotation, ast.Name) and arg.annotation.id == "object":
            yield _Violation(path, node.lineno, f"placeholder object annotation for '{arg.arg}'")


def test_suite_test_functions_use_explicit_fixture_types() -> None:
    violations: list[_Violation] = []
    for path in sorted(_SUITES_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in _iter_suite_test_functions(tree):
            violations.extend(_iter_param_violations(path, node))
            if node.returns is None or ast.unparse(node.returns) != "None":
                violations.append(
                    _Violation(path, node.lineno, "test functions must declare '-> None'")
                )

    assert not violations, "\n".join(
        f"{violation.path.relative_to(REPO_ROOT)}:{violation.line}: {violation.detail}"
        for violation in violations
    )
