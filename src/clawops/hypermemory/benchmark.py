"""Benchmark fixture loading for StrongClaw hypermemory."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence
from typing import cast

from clawops.common import load_yaml
from clawops.hypermemory.contracts import BenchmarkCase
from clawops.hypermemory.models import SearchMode
from clawops.typed_values import as_mapping


def load_benchmark_cases(path: pathlib.Path) -> list[BenchmarkCase]:
    """Load benchmark cases from a YAML fixture file."""
    raw = as_mapping(load_yaml(path), path=str(path))
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise TypeError("benchmark fixture must contain a cases list")
    return [
        _normalize_case(index, case) for index, case in enumerate(cast(Sequence[object], cases))
    ]


def _normalize_case(index: int, raw: object) -> BenchmarkCase:
    """Normalize a single benchmark case."""
    case_mapping = as_mapping(raw, path=f"cases[{index}]")
    name = _require_string(case_mapping.get("name"), f"cases[{index}].name")
    query = _require_string(case_mapping.get("query"), f"cases[{index}].query")
    expected_paths = _string_list(
        case_mapping.get("expectedPaths"),
        f"cases[{index}].expectedPaths",
    )
    lane_value = case_mapping.get("lane", "all")
    lane = cast(SearchMode, lane_value)
    if lane not in {"all", "memory", "corpus"}:
        raise ValueError(f"cases[{index}].lane must be all, memory, or corpus")
    case: BenchmarkCase = {
        "name": name,
        "query": query,
        "expectedPaths": expected_paths,
        "lane": lane,
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
    """Normalize a list of non-empty strings."""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be a list of strings")
    normalized: list[str] = []
    for item in cast(Sequence[object], value):
        if not isinstance(item, str) or not item.strip():
            raise TypeError(f"{name} must be a list of strings")
        normalized.append(item.strip())
    return normalized
