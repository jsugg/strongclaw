"""Hypermemory features: OpenClaw memory surface, hypermemory engine, plugin recall, Qdrant upkeep."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import cast

from clawops.qdrant_storage import QdrantRelease, StorageStamp, read_stamp, stamp_path, write_stamp
from clawops.strongclaw_runtime import (
    rendered_openclaw_hypermemory_config_path,
    resolve_compose_state_dir,
    resolve_openclaw_config_path,
)
from tests.utils.helpers.feature_verification import (
    CommandOutcome,
    FeatureHost,
    GatewayController,
    plugin_statuses,
)

AUX_COMPOSE = Path("platform/compose/docker-compose.aux-stack.yaml")
_QDRANT_IMAGE = re.compile(r"^\s*image:\s*(qdrant/qdrant:\S+)", re.MULTILINE)


def _config_path(feature_host: FeatureHost) -> Path:
    path = rendered_openclaw_hypermemory_config_path(
        resolve_openclaw_config_path(feature_host.repo_root)
    )
    assert path is not None and path.is_file(), "the hypermemory profile has no rendered config"
    return path


def _hypermemory(
    feature_host: FeatureHost, *arguments: str, timeout: float = 600
) -> CommandOutcome:
    return feature_host.clawops(
        "hypermemory", "--config", str(_config_path(feature_host)), *arguments, timeout=timeout
    )


def _status(feature_host: FeatureHost) -> dict[str, object]:
    return cast(
        dict[str, object], _hypermemory(feature_host, "status", "--json").require_ok().json()
    )


def _search(feature_host: FeatureHost, query: str) -> list[dict[str, object]]:
    payload = (
        _hypermemory(feature_host, "search", "--json", "--query", query, "--max-results", "5")
        .require_ok()
        .json()
    )
    return cast(list[dict[str, object]], cast(dict[str, object], payload)["results"])


def test_hypermemory_profile_plugins_are_loaded(feature_host: FeatureHost) -> None:
    statuses = plugin_statuses(feature_host)
    for plugin_id in ("strongclaw-hypermemory", "lossless-claw"):
        assert statuses.get(plugin_id) == "loaded", f"{plugin_id}: {statuses.get(plugin_id)}"
    plugins = cast(dict[str, object], feature_host.rendered_config()["plugins"])
    slots = cast(dict[str, object], plugins["slots"])
    assert slots == {"contextEngine": "lossless-claw", "memory": "strongclaw-hypermemory"}, slots


def test_openclaw_memory_status_is_healthy(feature_host: FeatureHost) -> None:
    outcome = feature_host.openclaw("memory", "status", "--deep", timeout=600).require_ok()
    assert (
        "strongclaw-hypermemory" in outcome.output or "hypermemory" in outcome.output
    ), outcome.describe()


def test_openclaw_memory_search_returns_corpus_hits(feature_host: FeatureHost) -> None:
    outcome = feature_host.openclaw(
        "memory", "search", "--query", "ClawOps", "--max-results", "1", timeout=600
    ).require_ok()
    assert ".md" in outcome.stdout, f"memory search returned no corpus hit\n{outcome.describe()}"


def test_hypermemory_index_and_hybrid_search(feature_host: FeatureHost) -> None:
    index = cast(
        dict[str, object], _hypermemory(feature_host, "index", "--json").require_ok().json()
    )
    assert int(cast(int, index["files"])) > 0 and int(cast(int, index["chunks"])) > 0, index
    status = _status(feature_host)
    assert status["backendActive"] == "qdrant_sparse_dense_hybrid", status
    assert int(cast(int, status["vectorItems"])) > 0, status
    assert int(cast(int, status["sparseVectorItems"])) > 0, status
    hits = _search(feature_host, "backup and recovery")
    assert hits, "hybrid search returned no hits for a shipped runbook topic"
    assert any("BACKUP" in str(hit["path"]).upper() for hit in hits), hits


def test_hypermemory_verify_reports_consistent_index(feature_host: FeatureHost) -> None:
    payload = cast(
        dict[str, object], _hypermemory(feature_host, "verify", "--json").require_ok().json()
    )
    assert payload["ok"] is True, payload


def test_hypermemory_store_search_and_forget(feature_host: FeatureHost) -> None:
    token = f"FV-store-{uuid.uuid4().hex[:10]}"
    text = f"The feature verification vault code is {token}."
    stored = _hypermemory(feature_host, "store", "--json", "--type", "fact", "--text", text)
    assert cast(dict[str, object], stored.require_ok().json()).get("ok", True) is not False
    assert any(token in str(hit.get("snippet")) for hit in _search(feature_host, token)), token
    forgot = _hypermemory(feature_host, "forget", "--json", "--entry-text", text).require_ok()
    assert cast(dict[str, object], forgot.json()).get("ok", True) is not False, forgot.describe()
    assert not any(token in str(hit.get("snippet")) for hit in _search(feature_host, token))


def test_hypermemory_maintenance_operations(feature_host: FeatureHost) -> None:
    for arguments in (
        ("reflect", "--json", "--mode", "safe"),
        ("lifecycle", "--json"),
        ("list-facts", "--json"),
        ("flush-metadata", "--json"),
    ):
        outcome = _hypermemory(feature_host, *arguments)
        assert outcome.ok, outcome.describe()
        assert isinstance(outcome.json(), dict | list), outcome.describe()


def test_hypermemory_exports_memory_pro_snapshot(feature_host: FeatureHost) -> None:
    output = feature_host.scratch("hypermemory-export") / "memory-pro.json"
    _hypermemory(feature_host, "export-memory-pro", "--json", "--output", str(output)).require_ok()
    assert output.is_file() and output.stat().st_size > 2, f"no export written to {output}"


def test_hypermemory_plugin_recalls_memory_into_turns(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    del gateway
    token = f"FV-recall-{uuid.uuid4().hex[:10]}"
    _hypermemory(
        feature_host,
        "store",
        "--json",
        "--type",
        "fact",
        "--text",
        f"The featver lighthouse passphrase is {token}.",
    ).require_ok()
    started = time.time()
    feature_host.openclaw(
        "agent",
        "--agent",
        "admin",
        "--session-id",
        f"featver-recall-{token}",
        "--message",
        "What is the featver lighthouse passphrase?",
        "--json",
        timeout=300,
    ).require_ok()
    prompts = [
        str(request.get("prompt", ""))
        for request in feature_host.fake_model_requests()
        if float(cast(float, request.get("at", 0.0))) >= started
    ]
    assert any(
        token in prompt for prompt in prompts
    ), "strongclaw-hypermemory autoRecall did not inject the stored fact into the model prompt"


def test_qdrant_storage_stamp_tracks_pinned_release(feature_host: FeatureHost) -> None:
    image_match = _QDRANT_IMAGE.search((feature_host.repo_root / AUX_COMPOSE).read_text("utf-8"))
    assert image_match is not None
    target = QdrantRelease.from_image(image_match.group(1))
    stamp = read_stamp(stamp_path(resolve_compose_state_dir()))
    assert stamp is not None, f"no qdrant stamp under {resolve_compose_state_dir()}"
    assert stamp.release_line == target.line and stamp.reindex_pending is False, stamp


def test_qdrant_release_change_resets_storage_and_reindexes(feature_host: FeatureHost) -> None:
    stamp_file = stamp_path(resolve_compose_state_dir())
    current = read_stamp(stamp_file)
    assert current is not None, f"no qdrant stamp at {stamp_file}"
    write_stamp(
        stamp_file,
        StorageStamp(
            release_line="0.0",
            image="qdrant/qdrant:v0.0.0",
            reindex_pending=False,
            updated_at=current.updated_at,
        ),
    )
    outcome = feature_host.clawops(
        "ops", "--asset-root", ".", "sidecars", "up", "--json", timeout=1500
    ).require_ok()
    assert "Resetting qdrant storage" in outcome.stderr, outcome.describe()
    command = cast(dict[str, object], cast(dict[str, object], outcome.json())["command"])
    steps = {
        str(cast(dict[str, object], step)["step"]) for step in cast(list[object], command["steps"])
    }
    assert {"reset-qdrant-storage", "reindex-hypermemory"} <= steps, steps
    restored = read_stamp(stamp_file)
    assert restored is not None and restored.release_line == current.release_line, restored
    assert restored.reindex_pending is False, restored
    assert int(cast(int, _status(feature_host)["vectorItems"])) > 0
