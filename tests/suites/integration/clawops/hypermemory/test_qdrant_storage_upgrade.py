"""Regression: storage written by Qdrant 1.15.5 upgrades to the pinned release via reset+reindex."""

from __future__ import annotations

import hashlib
import pathlib
import time
from dataclasses import replace
from typing import Any, cast

import pytest
import requests
import yaml

from clawops.hypermemory import HypermemoryEngine, load_config
from clawops.qdrant_storage import (
    StorageOperations,
    complete_reindex,
    prepare_storage,
    read_stamp,
    stamp_path,
)
from tests.plugins.infrastructure.mode import resolve_service_mode
from tests.utils.helpers.embeddings import DeterministicEmbeddingProvider
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config
from tests.utils.helpers.qdrant_release import QdrantProcess, fetch_release_binary, host_target
from tests.utils.helpers.repo import REPO_ROOT

pytestmark = [pytest.mark.qdrant, pytest.mark.network_local]

_LEGACY_RELEASE = "v1.15.5"
_COLLECTION = "hypermemory_upgrade_regression"
_LEGACY_COLLECTION = "legacy_default_layout"
_FLUSH_TIMEOUT_SECONDS = 30.0


@pytest.fixture(scope="module")
def release_binaries(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[pathlib.Path, pathlib.Path]:
    """Download the legacy and the repo-pinned Qdrant binaries (real mode only)."""
    if resolve_service_mode(request, "qdrant") != "real":
        pytest.skip("set QDRANT_TEST_MODE=real to run the Qdrant storage-upgrade regression")
    target = host_target()
    if target is None:
        pytest.skip("no Qdrant release binary for this platform")
    cache_dir = tmp_path_factory.mktemp("qdrant-releases")
    pinned = f"v{_pinned_image().split(':v', 1)[1].split('@', 1)[0]}"
    return (
        fetch_release_binary(_LEGACY_RELEASE, target=target, cache_dir=cache_dir),
        fetch_release_binary(pinned, target=target, cache_dir=cache_dir),
    )


def _pinned_image() -> str:
    compose = yaml.safe_load(
        (REPO_ROOT / "platform/compose/docker-compose.aux-stack.yaml").read_text(encoding="utf-8")
    )
    return str(cast(dict[str, Any], compose)["services"]["qdrant"]["image"])


def _engine(workspace: pathlib.Path, url: str) -> HypermemoryEngine:
    config_path = workspace / "hypermemory.sqlite.yaml"
    if not config_path.exists():
        write_hypermemory_config(workspace, config_path)
    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_sparse_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, url=url, collection=_COLLECTION),
    )
    return HypermemoryEngine(config, embedding_provider=DeterministicEmbeddingProvider())


def _seed_default_layout_collection(url: str, storage_dir: pathlib.Path) -> None:
    """Persist a RocksDB-backed payload index, the 1.15 storage 1.20 can no longer load.

    Hypermemory's own collection survives the jump (it creates no payload indexes), but operator
    Qdrant state can hold any collection; a flushed keyword index reproduces the upgrade panic.
    """
    base = f"{url}/collections/{_LEGACY_COLLECTION}"
    requests.put(
        base,
        json={"vectors": {"size": 4, "distance": "Cosine"}, "sparse_vectors": {"text": {}}},
        timeout=10.0,
    ).raise_for_status()
    points = [
        {
            "id": point_id,
            "vector": {"": [0.1, 0.2, 0.3, 0.4], "text": {"indices": [1, 7], "values": [0.5, 0.9]}},
            "payload": {"k": f"v{point_id % 3}"},
        }
        for point_id in range(1, 33)
    ]
    requests.put(
        f"{base}/points?wait=true", json={"points": points}, timeout=10.0
    ).raise_for_status()
    requests.put(
        f"{base}/index?wait=true",
        json={"field_name": "k", "field_schema": "keyword"},
        timeout=10.0,
    ).raise_for_status()
    configs = storage_dir / "collections" / _LEGACY_COLLECTION
    deadline = time.monotonic() + _FLUSH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if any(
            '"rocks_db"' in config.read_text(encoding="utf-8")
            for config in configs.glob("*/segments/*/payload_index/config.json")
        ):
            return
        time.sleep(0.5)
    pytest.fail("Qdrant 1.15 never flushed the RocksDB payload index to disk")


def _markdown_digest(workspace: pathlib.Path) -> dict[str, str]:
    return {
        str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(workspace.rglob("*.md"))
    }


def _compose_config(storage_dir: pathlib.Path) -> dict[str, object]:
    """Mirror `docker compose config --format json` for the Linux bind-mount variant."""
    return {
        "name": "strongclaw-sidecars-regression",
        "services": {
            "qdrant": {
                "image": _pinned_image(),
                "volumes": [
                    {"type": "bind", "source": str(storage_dir), "target": "/qdrant/storage"}
                ],
            }
        },
    }


def test_legacy_storage_is_reset_and_reindexed_on_upgrade(
    tmp_path: pathlib.Path, release_binaries: tuple[pathlib.Path, pathlib.Path]
) -> None:
    legacy_binary, pinned_binary = release_binaries
    state_dir = tmp_path / "compose-state"
    storage_dir = state_dir / "qdrant"
    storage_dir.mkdir(parents=True)
    workspace = build_workspace(tmp_path, include_daily_memory=False)
    log_path = tmp_path / "qdrant.log"

    with QdrantProcess(legacy_binary, storage_dir=storage_dir, log_path=log_path) as legacy:
        assert legacy.wait_healthy(), log_path.read_text(errors="replace")
        legacy_engine = _engine(workspace, legacy.url)
        legacy_engine.reindex()
        assert legacy_engine.search("gateway token", lane="all", auto_index=False)
        _seed_default_layout_collection(legacy.url, storage_dir)
    markdown_before = _markdown_digest(workspace)

    # The real failure: the pinned release refuses storage written by 1.15.5.
    legacy_log_size = log_path.stat().st_size
    with QdrantProcess(pinned_binary, storage_dir=storage_dir, log_path=log_path) as broken:
        assert not broken.wait_healthy(timeout_seconds=45)
    broken_log = log_path.read_bytes()[legacy_log_size:].decode(errors="replace")
    assert "unknown variant `rocks_db`" in broken_log, broken_log[-4000:]

    messages: list[str] = []
    stops: list[str] = []
    operations = StorageOperations(
        volume_exists=lambda name: pytest.fail(f"bind mount must not probe volume {name}"),
        stop_service=lambda: stops.append("qdrant"),
        remove_volume=lambda name: pytest.fail(f"bind mount must not remove volume {name}"),
        log=messages.append,
    )
    decision = prepare_storage(
        compose_config=_compose_config(storage_dir),
        compose_state_dir=state_dir,
        operations=operations,
    )
    assert decision.action == "reset"
    assert stops == ["qdrant"]
    assert messages and "Resetting qdrant storage" in messages[0]
    assert _markdown_digest(workspace) == markdown_before

    with QdrantProcess(pinned_binary, storage_dir=storage_dir, log_path=log_path) as upgraded:
        assert upgraded.wait_healthy(), log_path.read_text(errors="replace")
        engine = _engine(workspace, upgraded.url)

        def reindex() -> bool:
            engine.reindex()
            return True

        outcome = complete_reindex(compose_state_dir=state_dir, qdrant_ready=True, reindex=reindex)
        assert outcome == "reindexed"
        hits = engine.search("gateway token", lane="all", auto_index=False)
        assert hits
        assert hits[0].path == "docs/runbook.md"
        assert hits[0].backend == "qdrant_sparse_dense_hybrid"
        assert engine.status()["qdrantHealthy"] is True

        # A second start on matching storage changes nothing.
        again = prepare_storage(
            compose_config=_compose_config(storage_dir),
            compose_state_dir=state_dir,
            operations=operations,
        )
        assert again.action == "none"
        assert stops == ["qdrant"]
        assert (
            complete_reindex(
                compose_state_dir=state_dir,
                qdrant_ready=True,
                reindex=lambda: pytest.fail("matching storage must not reindex"),
            )
            == "not-needed"
        )
    stamp = read_stamp(stamp_path(state_dir))
    assert stamp is not None and not stamp.reindex_pending
    assert _markdown_digest(workspace) == markdown_before
