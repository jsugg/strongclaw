"""Unit tests for hypermemory/benchmark.py."""

from __future__ import annotations

import pathlib

import pytest

from clawops.hypermemory.benchmark import load_benchmark_cases

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: pathlib.Path, content: str) -> pathlib.Path:
    path = tmp_path / "benchmark.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Valid YAML parsing
# ---------------------------------------------------------------------------


def test_load_benchmark_cases_minimal_valid(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: simple\n    query: gateway token\n",
    )
    cases = load_benchmark_cases(path)
    assert len(cases) == 1
    assert cases[0]["name"] == "simple"
    assert cases[0]["query"] == "gateway token"
    assert cases[0]["expectedPaths"] == []


def test_load_benchmark_cases_with_expected_paths(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n"
        "  - name: with paths\n"
        "    query: deploy checklist\n"
        "    expectedPaths:\n"
        "      - docs/runbook.md\n"
        "      - bank/world.md\n",
    )
    cases = load_benchmark_cases(path)
    assert cases[0]["expectedPaths"] == ["docs/runbook.md", "bank/world.md"]


def test_load_benchmark_cases_default_lane_is_all(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: default lane\n    query: something\n",
    )
    cases = load_benchmark_cases(path)
    assert cases[0].get("lane") == "all"


def test_load_benchmark_cases_explicit_lane_memory(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: memory lane\n    query: fact\n    lane: memory\n",
    )
    cases = load_benchmark_cases(path)
    assert cases[0].get("lane") == "memory"


def test_load_benchmark_cases_explicit_lane_corpus(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: corpus lane\n    query: doc\n    lane: corpus\n",
    )
    cases = load_benchmark_cases(path)
    assert cases[0].get("lane") == "corpus"


def test_load_benchmark_cases_max_results_included(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: with max\n    query: q\n    maxResults: 5\n",
    )
    cases = load_benchmark_cases(path)
    assert cases[0].get("maxResults") == 5


def test_load_benchmark_cases_multiple_cases(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n" "  - name: first\n    query: alpha\n" "  - name: second\n    query: beta\n",
    )
    cases = load_benchmark_cases(path)
    assert len(cases) == 2
    assert cases[1]["name"] == "second"


def test_load_benchmark_cases_empty_cases_list(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(tmp_path, "cases: []\n")
    cases = load_benchmark_cases(path)
    assert cases == []


# ---------------------------------------------------------------------------
# Invalid cases
# ---------------------------------------------------------------------------


def test_load_benchmark_cases_missing_cases_key(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(tmp_path, "something: else\n")
    with pytest.raises(TypeError, match="cases list"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_missing_name(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(tmp_path, "cases:\n  - query: something\n")
    with pytest.raises(TypeError, match="name"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_empty_name(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(tmp_path, "cases:\n  - name: ''\n    query: something\n")
    with pytest.raises(TypeError, match="name"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_missing_query(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(tmp_path, "cases:\n  - name: has name\n")
    with pytest.raises(TypeError, match="query"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_bad_lane_value(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: bad lane\n    query: q\n    lane: hybrid\n",
    )
    with pytest.raises(ValueError, match="lane must be"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_non_positive_max_results(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: zero max\n    query: q\n    maxResults: 0\n",
    )
    with pytest.raises(TypeError, match="positive integer"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_negative_max_results(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: neg max\n    query: q\n    maxResults: -1\n",
    )
    with pytest.raises(TypeError, match="positive integer"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_expected_paths_not_list(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: bad paths\n    query: q\n    expectedPaths: not-a-list\n",
    )
    with pytest.raises(TypeError, match="list of strings"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_expected_paths_contains_non_string(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(
        tmp_path,
        "cases:\n  - name: bad item\n    query: q\n    expectedPaths:\n      - 123\n",
    )
    with pytest.raises(TypeError, match="list of strings"):
        load_benchmark_cases(path)


def test_load_benchmark_cases_not_a_mapping(tmp_path: pathlib.Path) -> None:
    path = _write_yaml(tmp_path, "- just a list\n")
    with pytest.raises((TypeError, ValueError)):
        load_benchmark_cases(path)
