"""Contract tests for the shipped codebase provider config."""

from __future__ import annotations

from clawops.context.codebase.benchmark import load_benchmark_cases
from clawops.context.codebase.service import load_config
from tests.utils.helpers.repo import REPO_ROOT


def test_shipped_codebase_provider_enables_hybrid_lane() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert config.embedding.enabled is True
    assert config.graph.backend == "neo4j"
    assert config.graph.neo4j_url == "bolt://127.0.0.1:7687"
    assert config.qdrant.enabled is True
    assert config.qdrant.collection == "strongclaw-codebase-context"
    assert config.hybrid.fusion == "rrf"


def test_shipped_codebase_benchmark_fixtures_load() -> None:
    cases = load_benchmark_cases(REPO_ROOT / "platform/configs/context/benchmarks/codebase.yaml")

    assert cases
    assert cases[0].get("expectedPaths")
