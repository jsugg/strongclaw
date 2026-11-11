"""memory-lancedb-pro features: plugin load, memory-pro CLI, migration tools, extraction, reflection."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from clawops.runtime_assets import resolve_runtime_layout
from tests.utils.helpers.feature_verification import (
    CommandOutcome,
    FeatureHost,
    GatewayController,
    plugin_statuses,
)

PLUGIN_ID = "memory-lancedb-pro"
SCOPE = "project:featver"
STRATEGY_KEY = f"plugins.entries.{PLUGIN_ID}.config.sessionStrategy"
MAIN_SESSION_KEY = "agent:admin:main"
EXTRACTION_TURNS = (
    "Remember that I prefer feature verification reports rendered as tables.",
    "I always run the lancedb lane on Ubuntu runners.",
    "My preferred review cadence for this project is weekly on Mondays.",
    "Our team name for the verification effort is Project Lighthouse.",
)
REFLECTION_TIMEOUT_SECONDS = 240.0
_POLL_SECONDS = 3.0


def _sqlite_config(feature_host: FeatureHost) -> Path:
    path = resolve_runtime_layout(repo_root=feature_host.repo_root).hypermemory_sqlite_config_path
    assert path.is_file(), f"setup did not materialize {path}"
    return path


def _seed_hypermemory_fact(feature_host: FeatureHost, token: str) -> None:
    feature_host.clawops(
        "hypermemory",
        "--config",
        str(_sqlite_config(feature_host)),
        "store",
        "--json",
        "--type",
        "fact",
        "--scope",
        SCOPE,
        "--text",
        f"The featver orchard code is {token}.",
    ).require_ok()


def _migrate(feature_host: FeatureHost, token: str) -> tuple[Path, dict[str, object]]:
    _seed_hypermemory_fact(feature_host, token)
    scratch = feature_host.scratch("memory-pro-migration")
    snapshot = scratch / "snapshot.json"
    outcome = feature_host.clawops(
        "memory",
        "migrate-hypermemory-to-pro",
        "--config",
        str(_sqlite_config(feature_host)),
        "--scope",
        SCOPE,
        "--output",
        str(snapshot),
        "--report",
        str(scratch / "report.json"),
    ).require_ok()
    return snapshot, cast(dict[str, object], outcome.json())


def _memory_pro_search(feature_host: FeatureHost, query: str) -> CommandOutcome:
    return feature_host.openclaw(
        "memory-pro", "search", query, "--scope", SCOPE, "--limit", "5", "--json", timeout=300
    )


def test_lancedb_plugin_is_loaded_in_the_memory_slot(feature_host: FeatureHost) -> None:
    assert plugin_statuses(feature_host).get(PLUGIN_ID) == "loaded"
    plugins = cast(dict[str, object], feature_host.rendered_config()["plugins"])
    assert cast(dict[str, object], plugins["slots"])["memory"] == PLUGIN_ID


def test_migrate_hypermemory_exports_memory_pro_snapshot(feature_host: FeatureHost) -> None:
    token = f"FV-migrate-{uuid.uuid4().hex[:10]}"
    snapshot, summary = _migrate(feature_host, token)
    assert summary["dryRun"] is False and summary["scope"] == SCOPE, summary
    assert token in snapshot.read_text(encoding="utf-8"), "snapshot is missing the seeded fact"


def test_import_pro_snapshot_lands_in_lancedb(feature_host: FeatureHost) -> None:
    token = f"FV-import-{uuid.uuid4().hex[:10]}"
    snapshot, _ = _migrate(feature_host, token)
    imported = feature_host.clawops(
        "memory", "import-pro-snapshot", "--input", str(snapshot), "--scope", SCOPE, timeout=600
    )
    assert cast(dict[str, object], imported.json())["ok"] is True, imported.describe()
    search = _memory_pro_search(feature_host, token).require_ok()
    assert token in search.stdout, f"memory-pro search cannot find the import\n{search.describe()}"


def test_verify_pro_parity_against_live_plugin(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    token = f"FV-parity-{uuid.uuid4().hex[:10]}"
    snapshot, _ = _migrate(feature_host, token)
    feature_host.clawops(
        "memory", "import-pro-snapshot", "--input", str(snapshot), "--scope", SCOPE, timeout=600
    ).require_ok()
    parity = feature_host.clawops(
        "memory",
        "verify-pro-parity",
        "--config",
        str(_sqlite_config(feature_host)),
        "--scope",
        SCOPE,
        "--mode",
        "openclaw",
        "--query",
        token,
        timeout=600,
    )
    payload = cast(dict[str, object], parity.json())
    assert parity.ok and payload["ok"] is True, parity.describe()


def test_smart_extraction_calls_the_extraction_model(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    del gateway
    started = time.time()
    session_id = f"featver-extract-{uuid.uuid4().hex[:8]}"
    for message in EXTRACTION_TURNS:
        feature_host.openclaw(
            "agent",
            "--agent",
            "admin",
            "--session-id",
            session_id,
            "--message",
            message,
            "--json",
            timeout=300,
        ).require_ok()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        requests = [
            request
            for request in feature_host.fake_model_requests()
            if float(cast(float, request.get("at", 0.0))) >= started
        ]
        if any(
            "extract memories worth long-term preservation" in str(r.get("prompt"))
            for r in requests
        ):
            assert any(r.get("path") == "/v1/embeddings" for r in requests), "no embedding call"
            return
        time.sleep(_POLL_SECONDS)
    raise AssertionError("autoCapture smart extraction never called the extraction model")


def _reflection_files(feature_host: FeatureHost) -> list[Path]:
    agents = cast(dict[str, object], feature_host.rendered_config()["agents"])
    admin = next(
        cast(dict[str, object], agent)
        for agent in cast(list[object], agents["list"])
        if cast(dict[str, object], agent)["id"] == "admin"
    )
    day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return sorted((Path(str(admin["workspace"])) / "memory" / "reflections" / day).glob("*.md"))


def test_reflection_runs_through_the_embedded_runner(
    feature_host: FeatureHost, gateway: GatewayController
) -> None:
    feature_host.openclaw("config", "set", STRATEGY_KEY, "memoryReflection").require_ok()
    try:
        gateway.restart()
        session_id = f"featver-reflect-{uuid.uuid4().hex[:8]}"
        feature_host.openclaw(
            "agent",
            "--agent",
            "admin",
            "--session-id",
            session_id,
            "--message",
            "We decided feature verification must use the real runtime.",
            "--json",
            timeout=300,
        ).require_ok()
        started = time.time()
        params = json.dumps({"key": MAIN_SESSION_KEY, "reason": "new"})
        feature_host.openclaw(
            "gateway", "call", "sessions.reset", "--params", params, "--json", timeout=300
        ).require_ok()
        deadline = time.monotonic() + REFLECTION_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            reflected = [
                path
                for path in _reflection_files(feature_host)
                if "FEATVER_REFLECTION_INVARIANT" in path.read_text(encoding="utf-8")
            ]
            if reflected:
                break
            time.sleep(_POLL_SECONDS)
        else:
            raise AssertionError("no reflection file was written after sessions.reset")
        prompts = [
            str(request.get("prompt"))
            for request in feature_host.fake_model_requests()
            if float(cast(float, request.get("at", 0.0))) >= started
        ]
        assert any("MEMORY REFLECTION" in prompt for prompt in prompts), "no reflection prompt"
        logs = gateway.log_text()
        assert "runner=embedded" in logs, (
            "reflection did not run through extensionAPI runEmbeddedPiAgent "
            f"(runner line: {[line for line in logs.splitlines() if 'runner=' in line][-3:]})"
        )
    finally:
        feature_host.openclaw("config", "set", STRATEGY_KEY, "none")
        gateway.restart()
