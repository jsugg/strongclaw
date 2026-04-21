"""Contract tests for the shipped codebase provider config."""

from __future__ import annotations

from clawops.context.codebase.benchmark import load_benchmark_cases
from clawops.context.codebase.service import load_config
from tests.utils.helpers.repo import REPO_ROOT


def test_shipped_codebase_provider_enables_hybrid_lane() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert config.embedding.enabled is True
    assert config.embedding.provider == "ollama-http"
    assert config.embedding.base_url == "http://127.0.0.1:11434"
    assert config.graph.backend == "neo4j"
    assert config.graph.neo4j_url == "bolt://127.0.0.1:7687"
    assert config.qdrant.enabled is True
    assert config.qdrant.collection == "strongclaw-codebase-context"
    assert config.hybrid.fusion == "rrf"


def test_shipped_codebase_benchmark_fixtures_load() -> None:
    cases = load_benchmark_cases(REPO_ROOT / "platform/configs/context/benchmarks/codebase.yaml")

    assert cases
    assert cases[0].get("expectedPaths")


def test_shipped_codebase_provider_excludes_non_source_runtime_mirrors() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert "src/clawops/assets/**" in config.exclude_globs
    assert "vendor/**" in config.exclude_globs
    assert "platform/configs/context/benchmarks/**" in config.exclude_globs


def test_shipped_codebase_provider_uses_conservative_local_embedding_settings() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert config.embedding.batch_size <= 8
    assert config.embedding.timeout_ms >= 30_000
