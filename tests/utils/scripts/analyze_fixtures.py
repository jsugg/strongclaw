"""Static analysis for fixture definitions and explicit suite references."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FixtureDefinition:
    """Static fixture definition metadata."""

    name: str
    file: str
    scope: str
    autouse: bool
    has_docstring: bool


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _fixture_source_files(repo_root: Path) -> list[Path]:
    fixture_files = sorted((repo_root / "tests" / "fixtures").rglob("*.py"))
    conftest_files = sorted((repo_root / "tests" / "suites").glob("**/conftest.py"))
    return [*fixture_files, *conftest_files]


def _test_source_files(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "tests" / "suites").glob("**/test_*.py"))


def _decorator_target_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        return _decorator_target_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_target_name(node.value)
        return node.attr if base is None else f"{base}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_fixture_decorator(node: ast.expr) -> bool:
    name = _decorator_target_name(node)
    return name in {"fixture", "pytest.fixture"}


def _extract_fixture_metadata(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, bool] | None:
    for decorator in function.decorator_list:
        if not _is_fixture_decorator(decorator):
            continue
        scope = "function"
        autouse = False
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "scope" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        scope = keyword.value.value
                if keyword.arg == "autouse" and isinstance(keyword.value, ast.Constant):
                    autouse = bool(keyword.value.value)
        return scope, autouse
    return None


def _extract_usefixtures(node: ast.expr) -> list[str]:
    if not isinstance(node, ast.Call):
        return []
    if _decorator_target_name(node.func) != "pytest.mark.usefixtures":
        return []
    names: list[str] = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.append(arg.value)
    return names


def collect_fixture_definitions(
    repo_root: Path | None = None,
) -> dict[str, list[FixtureDefinition]]:
    """Return fixture definitions grouped by name."""
    root = _repo_root() if repo_root is None else repo_root
    definitions: dict[str, list[FixtureDefinition]] = {}

    for path in _fixture_source_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            metadata = _extract_fixture_metadata(node)
            if metadata is None:
                continue
            scope, autouse = metadata
            definitions.setdefault(node.name, []).append(
                FixtureDefinition(
                    name=node.name,
                    file=path.relative_to(root).as_posix(),
                    scope=scope,
                    autouse=autouse,
                    has_docstring=ast.get_docstring(node) is not None,
                )
            )
    return definitions


def collect_fixture_references(
    fixture_names: set[str],
    repo_root: Path | None = None,
) -> dict[str, int]:
    """Count explicit fixture references in suite tests."""
    root = _repo_root() if repo_root is None else repo_root
    references = {name: 0 for name in fixture_names}

    for path in _test_source_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test_"):
                    for arg in node.args.args:
                        if arg.arg in references:
                            references[arg.arg] += 1
                for decorator in node.decorator_list:
                    for fixture_name in _extract_usefixtures(decorator):
                        if fixture_name in references:
                            references[fixture_name] += 1
            elif isinstance(node, ast.ClassDef):
                for decorator in node.decorator_list:
                    for fixture_name in _extract_usefixtures(decorator):
                        if fixture_name in references:
                            references[fixture_name] += 1
    return references


def analyze_fixture_tree(repo_root: Path | None = None) -> dict[str, Any]:
    """Analyze fixture definitions and references for the repository."""
    root = _repo_root() if repo_root is None else repo_root
    definitions = collect_fixture_definitions(root)
    references = collect_fixture_references(set(definitions), root)

    flattened = [definition for items in definitions.values() for definition in items]
    duplicates = [
        {"name": name, "locations": [definition.file for definition in items]}
        for name, items in sorted(definitions.items())
        if len(items) > 1
    ]
    fixtures = [
        {
            **asdict(definition),
            "references": references[definition.name],
        }
        for definition in sorted(flattened, key=lambda item: (item.file, item.name))
    ]

    return {
        "total_fixtures": len(definitions),
        "total_definitions": len(flattened),
        "total_references": sum(references.values()),
        "unused": sorted(name for name, count in references.items() if count == 0),
        "duplicates": duplicates,
        "undocumented": sorted(
            definition.name for definition in flattened if not definition.has_docstring
        ),
        "fixtures": fixtures,
    }


def _format_summary(report: dict[str, Any]) -> str:
    lines = [
        f"fixtures: {report['total_fixtures']}",
        f"definitions: {report['total_definitions']}",
        f"references: {report['total_references']}",
        f"duplicates: {len(report['duplicates'])}",
        f"undocumented: {len(report['undocumented'])}",
        f"unused: {len(report['unused'])}",
    ]
    for fixture in report["fixtures"]:
        lines.append(
            f"{fixture['name']}: scope={fixture['scope']} autouse={fixture['autouse']} "
            f"references={fixture['references']} file={fixture['file']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run fixture analysis and print either text or JSON output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    args = parser.parse_args(argv)

    report = analyze_fixture_tree()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_format_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
