"""Search-scope coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence
from dataclasses import replace

from clawops.hypermemory import HypermemoryEngine, load_config
from clawops.hypermemory.models import RerankResponse
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config


def test_hypermemory_scope_filter_and_explain(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    engine = HypermemoryEngine(load_config(config_path))
    engine.reindex()

    engine.store(
        kind="fact",
        text="Global browser-lab recovery stays local-only.",
        scope="project:strongclaw",
    )
    hits = engine.search(
        "browser-lab recovery",
        lane="memory",
        scope="project:strongclaw",
        include_explain=True,
    )

    assert hits
    assert hits[0].scope == "project:strongclaw"
    payload = hits[0].to_dict()
    assert payload["explain"]["lexicalScore"] > 0
    assert payload["scope"] == "project:strongclaw"


class _RecordingRerankProvider:
    """Score every candidate in descending order and record each rerank query."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def score(self, query: str, documents: Sequence[str]) -> RerankResponse:
        self.queries.append(query)
        count = len(documents)
        return RerankResponse(
            scores=tuple(float(count - index) for index in range(count)),
            provider="local-sentence-transformers",
            applied=True,
        )


def _reranking_engine(
    tmp_path: pathlib.Path,
) -> tuple[HypermemoryEngine, _RecordingRerankProvider]:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    config = load_config(config_path)
    config = replace(
        config,
        hybrid=replace(config.hybrid, rerank_candidate_pool=4),
        rerank=replace(config.rerank, enabled=True),
    )
    provider = _RecordingRerankProvider()
    engine = HypermemoryEngine(config, rerank_provider=provider)
    engine.reindex()
    return engine, provider


def test_hypermemory_search_reranks_candidates_by_default(tmp_path: pathlib.Path) -> None:
    """Explicit searches keep the configured semantic rerank stage."""
    engine, provider = _reranking_engine(tmp_path)

    hits = engine.search("deployment playbook")

    assert hits
    assert provider.queries == ["deployment playbook"]


def test_hypermemory_search_without_rerank_never_calls_the_rerank_provider(
    tmp_path: pathlib.Path,
) -> None:
    """The auto-recall fast path ranks by fusion alone and never touches the reranker."""
    engine, provider = _reranking_engine(tmp_path)

    hits = engine.search("deployment playbook", rerank=False)

    assert hits
    assert provider.queries == []
