"""Sidecar, observability and browser-lab features against the live compose stacks."""

from __future__ import annotations

import time
import uuid
from typing import cast

from clawops.strongclaw_runtime import set_env_assignment
from tests.utils.helpers.feature_verification import (
    OLLAMA_EMBEDDING_API_BASE,
    OLLAMA_EMBEDDING_MODEL,
    FeatureHost,
    basic_auth,
    http_request,
    tcp_open,
)

QDRANT = "http://127.0.0.1:6333"
LITELLM = "http://127.0.0.1:4000"
NEO4J_HTTP = "http://127.0.0.1:7474"
OTLP_HTTP = "http://127.0.0.1:4318"
OTEL_METRICS = "http://127.0.0.1:9464/metrics"
QDRANT_TEST_PREFIX = "memory-v2-int-"


def _container(feature_host: FeatureHost, fragment: str) -> str:
    listing = feature_host.run(["docker", "ps", "--format", "{{.Names}}"], timeout=60).require_ok()
    names = [name for name in listing.stdout.split() if fragment in name]
    assert names, f"no running container matches {fragment!r}: {listing.stdout}"
    return names[0]


def test_postgres_sidecar_accepts_queries(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    container = _container(feature_host, "postgres")
    outcome = feature_host.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "litellm",
            "-d",
            "litellm",
            "-tAc",
            "select count(*) from information_schema.tables",
        ],
        timeout=60,
    ).require_ok()
    assert int(outcome.stdout.strip()) > 0, outcome.describe()


def _embed_through_litellm(feature_host: FeatureHost, *, upstream_path: str) -> None:
    """Embed through the hypermemory-embedding alias and prove LiteLLM called its upstream."""
    assert http_request("GET", f"{LITELLM}/health/liveliness", timeout=10).status == 200
    started = time.time()
    response = http_request(
        "POST",
        f"{LITELLM}/v1/embeddings",
        body={"model": "hypermemory-embedding", "input": [f"FV-litellm-{uuid.uuid4().hex[:10]}"]},
        headers={"Authorization": f"Bearer {feature_host.env_values()['LITELLM_MASTER_KEY']}"},
        timeout=120,
    )
    assert response.status == 200, feature_host.redact(response.body[:2000])
    data = cast(list[dict[str, object]], cast(dict[str, object], response.json())["data"])
    assert data and len(cast(list[float], data[0]["embedding"])) > 0, response.body[:500]
    upstream = [
        request
        for request in feature_host.fake_model_requests()
        if float(cast(float, request.get("at", 0.0))) >= started
        and str(request.get("path", "")).startswith(upstream_path)
    ]
    assert upstream, f"litellm answered without calling its {upstream_path} upstream"


def _wait_for_litellm(timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    while http_request("GET", f"{LITELLM}/health/liveliness", timeout=10).status != 200:
        assert time.monotonic() < deadline, "litellm did not come back after recreation"
        time.sleep(3)


def test_litellm_routes_embeddings_to_the_upstream(
    feature_host: FeatureHost, sidecars: str
) -> None:
    del sidecars
    _embed_through_litellm(feature_host, upstream_path="/v1/embeddings")


def test_litellm_serves_the_default_ollama_embedding_lane(
    feature_host: FeatureHost, sidecars: str
) -> None:
    del sidecars
    env_file = feature_host.env_file()
    configured = feature_host.env_values()
    keys = ("HYPERMEMORY_EMBEDDING_MODEL", "HYPERMEMORY_EMBEDDING_API_BASE")
    # The lane `clawops varlock-env configure` selects when a local Ollama has the model.
    for key, value in zip(keys, (OLLAMA_EMBEDDING_MODEL, OLLAMA_EMBEDDING_API_BASE), strict=True):
        set_env_assignment(env_file, key, value)
    try:
        feature_host.clawops(
            "ops", "--asset-root", ".", "sidecars", "up", timeout=1500
        ).require_ok()
        _wait_for_litellm()
        _embed_through_litellm(feature_host, upstream_path="/api/embed")
    finally:
        for key in keys:
            set_env_assignment(env_file, key, configured.get(key, ""))
        feature_host.clawops("ops", "--asset-root", ".", "sidecars", "up", timeout=1500)
        _wait_for_litellm()


def test_qdrant_sidecar_round_trips_vectors(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    del feature_host
    collection = f"featver-{uuid.uuid4().hex[:8]}"
    assert http_request("GET", f"{QDRANT}/healthz").status == 200
    created = http_request(
        "PUT",
        f"{QDRANT}/collections/{collection}",
        body={"vectors": {"size": 4, "distance": "Cosine"}},
    )
    assert created.status == 200, created.body
    try:
        upsert = http_request(
            "PUT",
            f"{QDRANT}/collections/{collection}/points?wait=true",
            body={"points": [{"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"k": "fv"}}]},
        )
        assert upsert.status == 200, upsert.body
        search = http_request(
            "POST",
            f"{QDRANT}/collections/{collection}/points/search",
            body={"vector": [0.1, 0.2, 0.3, 0.4], "limit": 1, "with_payload": True},
        )
        hits = cast(list[dict[str, object]], cast(dict[str, object], search.json())["result"])
        assert hits and cast(dict[str, object], hits[0]["payload"])["k"] == "fv", search.body
    finally:
        http_request("DELETE", f"{QDRANT}/collections/{collection}")


def test_neo4j_sidecar_round_trips_cypher(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    values = feature_host.env_values()
    marker = f"fv-{uuid.uuid4().hex[:8]}"
    response = http_request(
        "POST",
        f"{NEO4J_HTTP}/db/neo4j/tx/commit",
        body={
            "statements": [
                {
                    "statement": "CREATE (n:FeatVer {id: $id}) RETURN n.id",
                    "parameters": {"id": marker},
                },
                {
                    "statement": "MATCH (n:FeatVer {id: $id}) DETACH DELETE n RETURN count(*)",
                    "parameters": {"id": marker},
                },
            ]
        },
        headers=basic_auth(values.get("NEO4J_USERNAME", "neo4j"), values["NEO4J_PASSWORD"]),
        timeout=60,
    )
    payload = cast(dict[str, object], response.json())
    assert response.status == 200 and not payload.get("errors"), response.body[:2000]
    results = cast(list[dict[str, object]], payload["results"])
    assert len(results) == 2, payload


def test_observability_pipeline_accepts_telemetry(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    feature_host.clawops("verify-platform", "observability", "--asset-root", ".").require_ok()
    trace = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "featver"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": uuid.uuid4().hex,
                                "spanId": uuid.uuid4().hex[:16],
                                "name": "featver-span",
                                "kind": 1,
                                "startTimeUnixNano": str(time.time_ns() - 1_000_000),
                                "endTimeUnixNano": str(time.time_ns()),
                            }
                        ]
                    }
                ],
            }
        ]
    }
    accepted = http_request("POST", f"{OTLP_HTTP}/v1/traces", body=trace)
    assert accepted.status == 200, accepted.body
    metric_name = f"featver_probe_{uuid.uuid4().hex[:8]}"
    gauge = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "featver"}}]
                },
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": metric_name,
                                "gauge": {
                                    "dataPoints": [
                                        {"asDouble": 1.0, "timeUnixNano": str(time.time_ns())}
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    assert http_request("POST", f"{OTLP_HTTP}/v1/metrics", body=gauge).status == 200
    deadline = time.monotonic() + 60
    metrics = http_request("GET", OTEL_METRICS)
    while metric_name not in metrics.body and time.monotonic() < deadline:
        time.sleep(2)
        metrics = http_request("GET", OTEL_METRICS)
    assert (
        metric_name in metrics.body
    ), f"OTLP metric never reached the exporter: {metrics.body[:500]}"


def test_verify_platform_sidecars_runtime(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    outcome = feature_host.clawops("verify-platform", "sidecars", "--asset-root", ".", timeout=300)
    payload = cast(dict[str, object], outcome.json())
    assert outcome.ok and payload["ok"] is True, outcome.describe()


def test_ops_status_reports_required_sidecars_ready(
    feature_host: FeatureHost, sidecars: str
) -> None:
    del sidecars
    payload = cast(
        dict[str, object],
        feature_host.clawops("ops", "--asset-root", ".", "status", timeout=300).require_ok().json(),
    )
    readiness = cast(dict[str, object], payload["readiness"])
    assert payload["ok"] is True and readiness["requiredReady"] is True, payload
    services = cast(dict[str, object], payload["services"])
    for name in ("postgres", "litellm", "qdrant", "neo4j"):
        assert cast(dict[str, object], services[name])["state"] == "running", services


def test_prune_qdrant_test_collections(feature_host: FeatureHost, sidecars: str) -> None:
    del sidecars
    collection = f"{QDRANT_TEST_PREFIX}featver-{uuid.uuid4().hex[:8]}"
    created = http_request(
        "PUT",
        f"{QDRANT}/collections/{collection}",
        body={"vectors": {"size": 4, "distance": "Dot"}},
    )
    assert created.status == 200, created.body
    dry_run = feature_host.clawops(
        "ops", "--asset-root", ".", "prune-qdrant-test-collections", "--dry-run"
    ).require_ok()
    assert collection in dry_run.stdout, dry_run.describe()
    assert http_request("GET", f"{QDRANT}/collections/{collection}").status == 200
    feature_host.clawops("ops", "--asset-root", ".", "prune-qdrant-test-collections").require_ok()
    assert http_request("GET", f"{QDRANT}/collections/{collection}").status == 404


def test_browser_lab_starts_and_exposes_cdp(feature_host: FeatureHost) -> None:
    feature_host.clawops("ops", "--asset-root", ".", "browser-lab", "up", timeout=900).require_ok()
    try:
        verify = feature_host.clawops(
            "verify-platform", "browser-lab", "--asset-root", ".", timeout=300
        )
        assert verify.ok and cast(dict[str, object], verify.json())["ok"] is True, verify.describe()
        deadline = time.monotonic() + 120
        version = http_request("GET", "http://127.0.0.1:9222/json/version", timeout=10)
        while version.status != 200 and time.monotonic() < deadline:
            time.sleep(3)
            version = http_request("GET", "http://127.0.0.1:9222/json/version", timeout=10)
        assert version.status == 200, version.body
        assert "webSocketDebuggerUrl" in version.body or "Browser" in version.body, version.body
        assert tcp_open(3128), "browser-lab egress proxy is not listening on 3128"
    finally:
        feature_host.clawops("ops", "--asset-root", ".", "browser-lab", "down", timeout=600)


def test_reset_compose_state_clears_one_component(feature_host: FeatureHost) -> None:
    state_root = feature_host.scratch("compose-state")
    component_dir = state_root / "browser-lab"
    component_dir.mkdir()
    (component_dir / "profile.db").write_text("stale", encoding="utf-8")
    outcome = feature_host.clawops(
        "ops",
        "--asset-root",
        ".",
        "reset-compose-state",
        "--component",
        "browser-lab",
        "--state-dir",
        str(state_root),
        timeout=180,
    ).require_ok()
    payload = cast(dict[str, object], outcome.json())
    assert payload["ok"] is True and payload["stateDir"] == str(component_dir), payload
    assert component_dir.is_dir() and not any(component_dir.iterdir()), list(
        component_dir.iterdir()
    )


def test_optional_ollama_compose_serves_the_api(feature_host: FeatureHost) -> None:
    compose = ["docker", "compose", "-f", "platform/compose/docker-compose.ollama.optional.yaml"]
    env = {"STRONGCLAW_COMPOSE_STATE_DIR": str(feature_host.scratch("ollama-state"))}
    feature_host.run([*compose, "up", "-d"], timeout=900, env=env).require_ok()
    try:
        deadline = time.monotonic() + 180
        version = http_request("GET", "http://127.0.0.1:11434/api/version", timeout=10)
        while version.status != 200 and time.monotonic() < deadline:
            time.sleep(3)
            version = http_request("GET", "http://127.0.0.1:11434/api/version", timeout=10)
        assert version.status == 200 and "version" in version.body, version.body
    finally:
        feature_host.run([*compose, "down", "-v"], timeout=300, env=env)
