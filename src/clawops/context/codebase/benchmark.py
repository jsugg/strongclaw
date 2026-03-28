"""Benchmark fixture loading for the codebase context provider."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence
from typing import TypedDict, cast

from clawops.common import load_yaml
from clawops.typed_values import as_mapping


class RequiredCodebaseBenchmarkCase(TypedDict):
    """Required benchmark case fields."""

    name: str
    query: str


class CodebaseBenchmarkCaseOptional(TypedDict, total=False):
    """Optional benchmark case fields."""

    expectedPaths: list[str]
    expectedChunkIds: list[str]
    maxResults: int


class CodebaseBenchmarkCase(RequiredCodebaseBenchmarkCase, CodebaseBenchmarkCaseOptional):
    """One benchmark case for codebase context retrieval."""


class CodebaseBenchmarkCaseResult(TypedDict):
    """One evaluated benchmark result row."""

    name: str
    query: str
    maxResults: int
    expectedPaths: list[str]
    expectedChunkIds: list[str]
    matchedPaths: list[str]
    matchedChunkIds: list[str]
    directRetrievedPaths: list[str]
    dependencyPaths: list[str]
    retrievedPaths: list[str]
    retrievedChunkIds: list[str]
    recallAtK: float
    mrr: float
    passed: bool


class CodebaseBenchmarkResult(TypedDict):
    """Public payload returned by ``CodebaseContextService.benchmark_cases``."""

    provider: str
    scale: str
    backendModes: list[str]
    total: int
    passed: int
    cases: list[CodebaseBenchmarkCaseResult]


def load_benchmark_cases(path: pathlib.Path) -> list[CodebaseBenchmarkCase]:
    """Load benchmark cases from a YAML fixture file."""
    raw = as_mapping(load_yaml(path), path=str(path))
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise TypeError("benchmark fixture must contain a cases list")
    return [
        _normalize_case(index, case) for index, case in enumerate(cast(Sequence[object], cases))
    ]


def _normalize_case(index: int, raw: object) -> CodebaseBenchmarkCase:
    """Normalize a single benchmark case."""
    case_mapping = as_mapping(raw, path=f"cases[{index}]")
    name = _require_string(case_mapping.get("name"), f"cases[{index}].name")
    query = _require_string(case_mapping.get("query"), f"cases[{index}].query")
    expected_paths = _string_list(
        case_mapping.get("expectedPaths"),
        f"cases[{index}].expectedPaths",
    )
    expected_chunk_ids = _string_list(
        case_mapping.get("expectedChunkIds"),
        f"cases[{index}].expectedChunkIds",
    )
    if not expected_paths and not expected_chunk_ids:
        raise TypeError(
            f"cases[{index}] must define expectedPaths or expectedChunkIds for evaluation"
        )

    case: CodebaseBenchmarkCase = {
        "name": name,
        "query": query,
        "expectedPaths": expected_paths,
        "expectedChunkIds": expected_chunk_ids,
    }
    max_results = case_mapping.get("maxResults")
    if max_results is not None:
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results <= 0:
            raise TypeError(f"cases[{index}].maxResults must be a positive integer")
        case["maxResults"] = max_results
    return case


def _require_string(value: object, name: str) -> str:
    """Require a non-empty string config value."""
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return value.strip()


def _string_list(value: object, name: str) -> list[str]:
    """Normalize a list of unique non-empty strings."""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in cast(Sequence[object], value):
        if not isinstance(item, str) or not item.strip():
            raise TypeError(f"{name} must be a list of strings")
        text = item.strip()
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized
