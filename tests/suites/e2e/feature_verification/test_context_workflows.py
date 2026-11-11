"""Context-service, workflow-runner and channel-config features against the real runtime."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import cast

from tests.utils.helpers.feature_verification import FeatureHost, basic_auth, http_request

CONTEXT_CONFIG = "platform/configs/context/codebase.yaml"
CHANNELS_OVERLAY = "platform/configs/openclaw/30-channels.json5"
NEO4J_HTTP = "http://127.0.0.1:7474/db/neo4j/tx/commit"


def _sample_repo(feature_host: FeatureHost, marker: str) -> Path:
    repo = feature_host.scratch("context-repo")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "ledger.py").write_text(
        f'"""Ledger module {marker}."""\n\n\n'
        "class LedgerReconciler:\n"
        "    def reconcile(self, entries: list[int]) -> int:\n"
        "        return sum(entries)\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text(f"# Ledger\n\nReconciliation notes {marker}.\n", "utf-8")
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "."],
        ["git", "-c", "user.email=fv@example.com", "-c", "user.name=fv", "commit", "-qm", "seed"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    return repo


def _context_env(feature_host: FeatureHost) -> dict[str, str]:
    values = feature_host.env_values()
    keys = (
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "HYPERMEMORY_QDRANT_URL",
        "HYPERMEMORY_EMBEDDING_MODEL",
    )
    return {key: values[key] for key in keys if values.get(key)}


def _neo4j_node_count(feature_host: FeatureHost) -> int:
    values = feature_host.env_values()
    response = http_request(
        "POST",
        NEO4J_HTTP,
        body={"statements": [{"statement": "MATCH (n) RETURN count(n) AS nodes"}]},
        headers=basic_auth(values.get("NEO4J_USERNAME", "neo4j"), values["NEO4J_PASSWORD"]),
        timeout=60,
    )
    assert response.status == 200, response.body[:1000]
    results = cast(list[dict[str, object]], cast(dict[str, object], response.json())["results"])
    data = cast(list[dict[str, object]], results[0]["data"])
    return int(cast(list[int], data[0]["row"])[0])


def test_codebase_context_indexes_into_the_neo4j_graph(feature_host: FeatureHost) -> None:
    marker = f"fv{uuid.uuid4().hex[:8]}"
    repo = _sample_repo(feature_host, marker)
    env = _context_env(feature_host)
    common = ("--config", CONTEXT_CONFIG, "--repo", str(repo), "--scale", "large")
    index = feature_host.clawops("context", "codebase", "index", *common, "--json", env=env)
    stats = cast(dict[str, object], index.require_ok().json())
    assert int(cast(int, stats["total_files"])) >= 2, stats
    assert _neo4j_node_count(feature_host) > 0, "large-scale indexing wrote nothing to neo4j"
    query = feature_host.clawops(
        "context", "codebase", "query", *common, "--query", "LedgerReconciler reconcile", env=env
    ).require_ok()
    assert "pkg/ledger.py" in query.stdout, query.describe()
    pack_path = feature_host.scratch("context-pack") / "pack.md"
    feature_host.clawops(
        "context",
        "codebase",
        "pack",
        *common,
        "--query",
        "reconcile",
        "--output",
        str(pack_path),
        env=env,
    ).require_ok()
    assert "LedgerReconciler" in pack_path.read_text(encoding="utf-8")


def test_daily_healthcheck_workflow_runs_openclaw_checks(feature_host: FeatureHost) -> None:
    outcome = feature_host.clawops(
        "workflow", "--workflow", "platform/configs/workflows/daily_healthcheck.yaml", timeout=1200
    )
    rows = [line.split("\t") for line in outcome.stdout.splitlines() if "\t" in line]
    results = {row[0]: row[1] for row in rows if len(row) >= 2}
    assert results == {"openclaw doctor": "ok", "security audit": "ok"}, outcome.describe()
    assert outcome.ok, outcome.describe()


def test_channels_overlay_validates_under_openclaw(feature_host: FeatureHost) -> None:
    scratch = feature_host.scratch("channels")
    rendered = scratch / "openclaw.json"
    feature_host.clawops(
        "render-openclaw-config",
        "--asset-root",
        ".",
        "--home-dir",
        str(feature_host.home),
        "--profile",
        "openclaw-default",
        "--overlay",
        CHANNELS_OVERLAY,
        "--output",
        str(rendered),
        "--exec-approvals-output",
        str(scratch / "exec-approvals.json"),
    ).require_ok()
    env = {"OPENCLAW_CONFIG_PATH": str(rendered), "TELEGRAM_BOT_TOKEN": "123456:featver-stub-token"}
    feature_host.openclaw("config", "validate", env=env, timeout=120).require_ok()
    listed = feature_host.openclaw("channels", "list", "--no-usage", "--json", env=env, timeout=180)
    payload = json.dumps(listed.require_ok().json())
    assert "telegram" in payload and "whatsapp" in payload, payload[:2000]
