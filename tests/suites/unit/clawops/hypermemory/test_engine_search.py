"""Search-scope coverage for the StrongClaw hypermemory engine."""

from __future__ import annotations

import pathlib

from clawops.hypermemory import HypermemoryEngine, load_config
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
