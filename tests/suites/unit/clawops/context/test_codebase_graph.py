"""Unit tests for codebase context graph policy."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import write_yaml
from clawops.context.codebase.service import (
    EdgeRecord,
    GraphConfig,
    GraphNode,
    Neo4jGraphBackend,
    normalize_neo4j_driver_url,
    service_from_config,
)
from tests.plugins.infrastructure.context import TestContext


def test_large_scale_requires_healthy_neo4j_even_when_fallback_is_allowed(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run_review():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {
                "enabled": True,
                "backend": "neo4j",
                "allow_degraded_fallback": True,
            },
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
        },
    )

    service = service_from_config(config_path, repo, scale="large")

    with pytest.raises(RuntimeError, match="requires a healthy neo4j graph backend"):
        service.index()


def test_medium_scale_requires_healthy_neo4j_when_degraded_fallback_is_disabled(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run_review():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {
                "enabled": True,
                "backend": "neo4j",
                "allow_degraded_fallback": False,
            },
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
        },
    )

    service = service_from_config(config_path, repo, scale="medium")

    with pytest.raises(
        RuntimeError,
        match="requires a healthy neo4j graph backend when degraded fallback is disabled",
    ):
        service.index()


def test_medium_scale_degrades_to_sqlite_when_fallback_is_allowed(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run_review():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {
                "enabled": True,
                "backend": "neo4j",
                "allow_degraded_fallback": True,
            },
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
        },
    )

    service = service_from_config(config_path, repo, scale="medium")

    assert service.index() == 1
    assert service.backend_modes() == ("lexical", "graph")


def test_normalize_neo4j_driver_url_converts_legacy_http_endpoint() -> None:
    assert normalize_neo4j_driver_url("http://127.0.0.1:7474") == "bolt://127.0.0.1:7687"
    assert normalize_neo4j_driver_url("bolt://127.0.0.1:7687") == "bolt://127.0.0.1:7687"


def test_medium_scale_symbol_graph_expands_related_files(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "provider.py").write_text(
        "def rotate_token():\n    return 'secret rotation provider'\n",
        encoding="utf-8",
    )
    (repo / "consumer.py").write_text(
        "def dispatch():\n    return rotate_token()\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "graph": {"enabled": True, "backend": "sqlite"},
            "embedding": {"enabled": False, "provider": "disabled"},
            "qdrant": {"enabled": False},
        },
    )

    service = service_from_config(config_path, repo, scale="medium")

    assert service.index() == 2
    pack = service.pack("secret rotation provider", limit=1)

    assert "## Dependency expansion" in pack
    assert "consumer.py" in pack
    with service.connect() as conn:
        edge_types = {
            str(row["edge_type"])
            for row in conn.execute(
                "SELECT DISTINCT edge_type FROM edges ORDER BY edge_type ASC"
            ).fetchall()
        }
    assert {"CALLS", "DEFINES", "REFERENCES"} <= edge_types


def test_neo4j_neighbors_use_literal_validated_depth(
    test_context: TestContext,
) -> None:
    backend = Neo4jGraphBackend(GraphConfig())
    recorded_query = ""
    recorded_parameters: dict[str, object] = {}

    def _fake_run_query(
        query: str,
        *,
        parameters: dict[str, object],
    ) -> list[dict[str, object]]:
        nonlocal recorded_query, recorded_parameters
        recorded_query = query
        recorded_parameters = parameters
        return [{"id": "neighbor-1"}]

    test_context.patch.patch_object(backend, "_run_query", new=_fake_run_query)

    neighbors = backend.neighbors(
        node_id="symbol:provider.rotate_token",
        edge_types=["CALLS"],
        depth=2,
        limit=4,
    )

    assert neighbors == ["neighbor-1"]
    assert "*1..2" in recorded_query
    assert "depth" not in recorded_parameters


def test_neo4j_neighbors_reject_invalid_depth() -> None:
    backend = Neo4jGraphBackend(GraphConfig())

    with pytest.raises(ValueError, match="depth"):
        backend.neighbors(
            node_id="symbol:provider.rotate_token",
            edge_types=["CALLS"],
            depth=0,
            limit=4,
        )


def test_neo4j_upsert_prunes_stale_edges_and_reports_cleanup_counts(
    test_context: TestContext,
) -> None:
    backend = Neo4jGraphBackend(GraphConfig())
    seen_queries: list[str] = []

    def _fake_run_query(
        query: str,
        *,
        parameters: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        del parameters
        seen_queries.append(query)
        if "deleted_edges" in query:
            return [{"deleted_edges": 2}]
        if "deleted_nodes" in query:
            return [{"deleted_nodes": 1}]
        return []

    test_context.patch.patch_object(backend, "_run_query", new=_fake_run_query)

    cleanup = backend.upsert(
        nodes=[
            GraphNode(
                node_id="symbol:provider.rotate_token",
                path="provider.py",
                language="python",
                kind="symbol",
            ),
            GraphNode(
                node_id="symbol:consumer.dispatch",
                path="consumer.py",
                language="python",
                kind="symbol",
            ),
        ],
        edges=[
            EdgeRecord(
                src_id="symbol:consumer.dispatch",
                dst_id="symbol:provider.rotate_token",
                edge_type="CALLS",
                path="consumer.py",
                weight=1,
            )
        ],
        snapshot_id="snapshot-2",
    )

    assert cleanup.deleted_edges == 2
    assert cleanup.deleted_nodes == 1
    assert any("MATCH ()-[rel:CODE_EDGE]->()" in query for query in seen_queries)
    assert any("rel.snapshot_id <> $snapshot_id" in query for query in seen_queries)
