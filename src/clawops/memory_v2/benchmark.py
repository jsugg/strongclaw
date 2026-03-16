"""Benchmark fixture loading for strongclaw memory v2."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from clawops.common import load_yaml


def load_benchmark_cases(path: pathlib.Path) -> list[dict[str, Any]]:
    """Load benchmark cases from a YAML fixture file."""
    raw = load_yaml(path)
    if not isinstance(raw, Mapping):
        raise TypeError("benchmark fixture root must be a mapping")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise TypeError("benchmark fixture must contain a cases list")
    return [_normalize_case(index, case) for index, case in enumerate(cases)]


def _normalize_case(index: int, raw: object) -> dict[str, Any]:
    """Normalize a single benchmark case."""
    if not isinstance(raw, Mapping):
        raise TypeError(f"cases[{index}] must be a mapping")
    name = _require_string(raw.get("name"), f"cases[{index}].name")
    query = _require_string(raw.get("query"), f"cases[{index}].query")
    expected_paths = _string_list(raw.get("expectedPaths"), f"cases[{index}].expectedPaths")
    lane = raw.get("lane", "all")
    if lane not in {"all", "memory", "corpus"}:
        raise ValueError(f"cases[{index}].lane must be all, memory, or corpus")
    case: dict[str, Any] = {
        "name": name,
        "query": query,
        "expectedPaths": expected_paths,
        "lane": lane,
    }
    max_results = raw.get("maxResults")
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
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TypeError(f"{name} must be a list of strings")
        normalized.append(item.strip())
    return normalized
