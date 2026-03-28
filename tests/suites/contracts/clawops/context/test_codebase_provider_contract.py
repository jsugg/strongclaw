"""Contract tests for the shipped codebase provider config."""

from __future__ import annotations

from clawops.context.codebase.benchmark import load_benchmark_cases
from clawops.context.codebase.service import load_config
from tests.utils.helpers.repo import REPO_ROOT


def _context_service_docs() -> str:
    return (REPO_ROOT / "platform/docs/CONTEXT_SERVICE.md").read_text(encoding="utf-8")


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


def test_shipped_codebase_provider_excludes_non_source_runtime_mirrors() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert "src/clawops/assets/**" in config.exclude_globs
    assert "vendor/**" in config.exclude_globs
    assert "platform/configs/context/benchmarks/**" in config.exclude_globs


def test_shipped_codebase_provider_uses_conservative_local_embedding_settings() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert config.embedding.batch_size <= 8
    assert config.embedding.timeout_ms >= 30_000


def test_context_service_docs_define_small_as_lexical_first() -> None:
    docs = _context_service_docs()

    assert "`small` keeps the file-level lexical path and avoids graph expansion" in docs
    assert (
        "For `small`, benchmark cases should use exact lexical or symbol-oriented queries." in docs
    )


def test_shipped_codebase_benchmark_examples_route_semantic_expectations_to_medium_or_large() -> (
    None
):
    docs = _context_service_docs()

    assert (
        "semantic or paraphrase-oriented benchmark cases should target `medium` or `large`." in docs
    )
    assert "clawops context codebase benchmark --scale medium" in docs
    assert "final context surface" in docs
    assert "dependency expansion" in docs
    assert "the benchmark command excludes" in docs
    assert "query set cannot self-match" in docs
    assert "src/clawops/assets/**" in docs
    assert "keeps embedding batches conservative and allows longer HTTP timeouts" in docs
    assert "resumes from the remaining chunks" in docs
