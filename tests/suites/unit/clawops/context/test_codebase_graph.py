"""Unit tests for codebase context graph policy."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import write_yaml
from clawops.context.codebase.service import normalize_neo4j_driver_url, service_from_config


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
