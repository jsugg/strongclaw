"""Unit tests for the lexical context service."""

from __future__ import annotations

import pathlib

from clawops.common import write_yaml
from clawops.context_service import service_from_config


def test_index_and_query(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "class AuthService:\n    def validate_jwt(self):\n        return True\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})
    service = service_from_config(config_path, repo)
    count = service.index()
    assert count == 1
    hits = service.query("validate_jwt", limit=3)
    assert hits
    assert hits[0].path == "auth.py"
