"""Unit tests for codebase context graph policy."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import write_yaml
from clawops.context.codebase.service import service_from_config


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
