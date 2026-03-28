"""Unit tests for codebase context benchmarking."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.common import write_yaml
from clawops.context.codebase.benchmark import load_benchmark_cases
from clawops.context.codebase.service import main, service_from_config


def _write_codebase_config(path: pathlib.Path) -> None:
    write_yaml(
        path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {"enabled": False},
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
            "paths": {"include": ["**/*.py"]},
        },
    )


def _write_codebase_config_with_sqlite_graph(path: pathlib.Path) -> None:
    write_yaml(
        path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {"enabled": True, "backend": "sqlite"},
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
            "paths": {"include": ["**/*.py"]},
        },
    )


def _write_codebase_config_with_yaml(path: pathlib.Path) -> None:
    write_yaml(
        path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {"enabled": False},
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
            "paths": {"include": ["**/*.py", "**/*.yaml"]},
        },
    )


def test_load_codebase_benchmark_cases_requires_expected_targets(
    tmp_path: pathlib.Path,
) -> None:
    fixtures_path = tmp_path / "benchmark.yaml"
    fixtures_path.write_text(
        "cases:\n" "  - name: broken\n" "    query: token guard\n",
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="expectedPaths or expectedChunkIds"):
        load_benchmark_cases(fixtures_path)


def test_codebase_benchmark_runner_reports_path_metrics(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def token_guard():\n    return 'credential rotation'\n",
        encoding="utf-8",
    )
    (repo / "notes.py").write_text(
        "def review_notes():\n    return 'notes'\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    _write_codebase_config(config_path)
    service = service_from_config(config_path, repo, scale="small")
    service.index()

    payload = service.benchmark_cases(
        [{"name": "auth path", "query": "credential rotation", "expectedPaths": ["auth.py"]}]
    )

    assert payload["provider"] == "codebase"
    assert payload["scale"] == "small"
    assert payload["passed"] == 1
    assert payload["cases"][0]["matchedPaths"] == ["auth.py"]
    assert payload["cases"][0]["retrievedPaths"][0] == "auth.py"
    assert payload["cases"][0]["directRetrievedPaths"] == ["auth.py"]
    assert payload["cases"][0]["dependencyPaths"] == []
    assert payload["cases"][0]["recallAtK"] == 1.0
    assert payload["cases"][0]["mrr"] == 1.0


def test_codebase_benchmark_runner_supports_chunk_expectations(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def token_guard():\n    return 'credential rotation'\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    _write_codebase_config(config_path)
    service = service_from_config(config_path, repo, scale="medium")
    service.index()
    chunk_hit = service.query("credential rotation", limit=1)[0]
    assert chunk_hit.chunk_id is not None

    payload = service.benchmark_cases(
        [
            {
                "name": "auth chunk",
                "query": "credential rotation",
                "expectedChunkIds": [chunk_hit.chunk_id],
            }
        ]
    )

    assert payload["passed"] == 1
    assert payload["cases"][0]["matchedChunkIds"] == [chunk_hit.chunk_id]
    assert payload["cases"][0]["retrievedChunkIds"][0] == chunk_hit.chunk_id


def test_codebase_benchmark_runner_matches_dependency_expansion_paths(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "workflow_runner.py").write_text(
        "from orchestration import run_pipeline\n\n"
        "def build_context_pack():\n"
        "    return run_pipeline()\n",
        encoding="utf-8",
    )
    (repo / "orchestration.py").write_text(
        "def run_pipeline():\n" "    return 'ok'\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    _write_codebase_config_with_sqlite_graph(config_path)
    service = service_from_config(config_path, repo, scale="medium")
    service.index()

    payload = service.benchmark_cases(
        [
            {
                "name": "workflow expansion",
                "query": "build_context_pack",
                "expectedPaths": ["workflow_runner.py", "orchestration.py"],
            }
        ]
    )

    assert payload["passed"] == 1
    assert payload["cases"][0]["matchedPaths"] == [
        "workflow_runner.py",
        "orchestration.py",
    ]
    assert payload["cases"][0]["directRetrievedPaths"] == ["workflow_runner.py"]
    assert payload["cases"][0]["dependencyPaths"] == ["orchestration.py"]
    assert payload["cases"][0]["retrievedPaths"] == [
        "workflow_runner.py",
        "orchestration.py",
    ]
    assert payload["cases"][0]["recallAtK"] == 1.0
    assert payload["cases"][0]["mrr"] == 1.0


def test_codebase_context_cli_benchmark_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def token_guard():\n    return 'credential rotation'\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    _write_codebase_config(config_path)
    fixtures_path = tmp_path / "benchmark.yaml"
    fixtures_path.write_text(
        "cases:\n"
        "  - name: auth path\n"
        "    query: credential rotation\n"
        "    expectedPaths:\n"
        "      - auth.py\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "benchmark",
            "--config",
            str(config_path),
            "--repo",
            str(repo),
            "--scale",
            "small",
            "--fixtures",
            str(fixtures_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["provider"] == "codebase"
    assert payload["passed"] == 1
    assert payload["cases"][0]["matchedPaths"] == ["auth.py"]


def test_codebase_context_cli_benchmark_excludes_fixture_file_from_index(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def token_guard():\n    return 'credential rotation'\n",
        encoding="utf-8",
    )
    fixtures_dir = repo / "benchmarks"
    fixtures_dir.mkdir()
    fixtures_path = fixtures_dir / "benchmark.yaml"
    fixtures_path.write_text(
        "cases:\n"
        "  - name: auth path\n"
        "    query: credential rotation\n"
        "    expectedPaths:\n"
        "      - auth.py\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    _write_codebase_config_with_yaml(config_path)

    exit_code = main(
        [
            "benchmark",
            "--config",
            str(config_path),
            "--repo",
            str(repo),
            "--scale",
            "small",
            "--fixtures",
            str(fixtures_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["passed"] == 1
    assert payload["cases"][0]["matchedPaths"] == ["auth.py"]
    assert "benchmarks/benchmark.yaml" not in payload["cases"][0]["retrievedPaths"]
