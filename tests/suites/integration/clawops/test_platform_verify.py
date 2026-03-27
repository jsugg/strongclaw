"""Tests for platform verification commands."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import load_yaml, write_text, write_yaml
from clawops.platform_verify import verify_channels, verify_observability, verify_sidecars
from tests.utils.helpers.network import Endpoint
from tests.utils.helpers.network_runtime import NetworkRuntime
from tests.utils.helpers.repo import REPO_ROOT

pytestmark = pytest.mark.network_local


def _port_mapping(endpoint: Endpoint, target_port: int) -> str:
    host, port = endpoint
    return f"{host}:{port}:{target_port}"


def _write_sidecars_compose(
    compose_path: pathlib.Path,
    *,
    postgres: Endpoint,
    litellm: Endpoint,
    otlp: Endpoint,
    metrics: Endpoint,
    qdrant: Endpoint,
    neo4j_http: Endpoint,
    neo4j_bolt: Endpoint,
) -> None:
    write_yaml(
        compose_path,
        {
            "services": {
                "postgres": {
                    "ports": [_port_mapping(postgres, 5432)],
                    "healthcheck": {"test": ["CMD", "true"]},
                },
                "litellm": {
                    "ports": [_port_mapping(litellm, 4000)],
                    "healthcheck": {"test": ["CMD", "true"]},
                },
                "otel-collector": {
                    "ports": [
                        _port_mapping(otlp, 4318),
                        _port_mapping(metrics, 9464),
                    ]
                },
                "qdrant": {
                    "ports": [_port_mapping(qdrant, 6333)],
                    "healthcheck": {"test": ["CMD", "true"]},
                },
                "neo4j": {
                    "ports": [
                        _port_mapping(neo4j_http, 7474),
                        _port_mapping(neo4j_bolt, 7687),
                    ],
                    "healthcheck": {"test": ["CMD", "true"]},
                },
            }
        },
    )


def _write_observability_inputs(
    overlay_path: pathlib.Path,
    compose_path: pathlib.Path,
    *,
    otlp: Endpoint,
    metrics: Endpoint,
) -> None:
    otlp_host, otlp_port = otlp
    write_text(
        overlay_path,
        (
            "{\n"
            '  "diagnostics": {"enabled": true, "otel": {"enabled": true, "endpoint": '
            f'"http://{otlp_host}:{otlp_port}", "protocol": "http/protobuf", '
            '"metrics": true, "traces": true}},\n'
            '  "logging": {"redactSensitive": "tools", "redactPatterns": ["sk-.*"]},\n'
            '  "plugins": {"allow": ["diagnostics-otel"], "entries": {"diagnostics-otel": {"enabled": true}}}\n'
            "}\n"
        ),
    )
    write_yaml(
        compose_path,
        {
            "services": {
                "otel-collector": {
                    "ports": [
                        _port_mapping(otlp, 4318),
                        _port_mapping(metrics, 9464),
                    ]
                }
            }
        },
    )


def test_verify_sidecars_supports_runtime_probes(
    tmp_path: pathlib.Path,
    network_runtime: NetworkRuntime,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    _write_sidecars_compose(
        compose_path,
        postgres=network_runtime.tcp_listener(),
        litellm=network_runtime.http_listener(b"alive\n"),
        otlp=network_runtime.tcp_listener(),
        metrics=network_runtime.http_listener(b"otel metrics\n"),
        qdrant=network_runtime.http_listener(b"ok\n"),
        neo4j_http=network_runtime.http_listener(b"neo4j\n"),
        neo4j_bolt=network_runtime.tcp_listener(),
    )

    report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
    assert report.ok is True


def test_verify_sidecars_reports_http_disconnects_without_crashing(
    tmp_path: pathlib.Path,
    network_runtime: NetworkRuntime,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    _write_sidecars_compose(
        compose_path,
        postgres=network_runtime.tcp_listener(),
        litellm=network_runtime.disconnecting_listener(),
        otlp=network_runtime.tcp_listener(),
        metrics=network_runtime.http_listener(b"otel metrics\n"),
        qdrant=network_runtime.http_listener(b"ok\n"),
        neo4j_http=network_runtime.http_listener(b"neo4j\n"),
        neo4j_bolt=network_runtime.tcp_listener(),
    )

    report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
    assert report.ok is False
    litellm_check = next(check for check in report.checks if check.name == "litellm-runtime")
    assert litellm_check.ok is False
    assert "http reachability failed" in litellm_check.message


def test_verify_sidecars_reports_qdrant_runtime_failures(
    tmp_path: pathlib.Path,
    network_runtime: NetworkRuntime,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    _write_sidecars_compose(
        compose_path,
        postgres=network_runtime.tcp_listener(),
        litellm=network_runtime.http_listener(b"alive\n"),
        otlp=network_runtime.tcp_listener(),
        metrics=network_runtime.http_listener(b"otel metrics\n"),
        qdrant=network_runtime.disconnecting_listener(),
        neo4j_http=network_runtime.http_listener(b"neo4j\n"),
        neo4j_bolt=network_runtime.tcp_listener(),
    )

    report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
    assert report.ok is False
    qdrant_check = next(check for check in report.checks if check.name == "qdrant-runtime")
    assert qdrant_check.ok is False
    assert "http reachability failed" in qdrant_check.message


def test_verify_observability_supports_runtime_probes(
    tmp_path: pathlib.Path,
    network_runtime: NetworkRuntime,
) -> None:
    overlay_path = tmp_path / "50-observability.json5"
    compose_path = tmp_path / "compose.yaml"
    _write_observability_inputs(
        overlay_path,
        compose_path,
        otlp=network_runtime.tcp_listener(),
        metrics=network_runtime.http_listener(b"otel metrics\n"),
    )

    report = verify_observability(
        overlay_path=overlay_path,
        compose_path=compose_path,
        skip_runtime=False,
    )
    assert report.ok is True


def test_verify_observability_accepts_empty_metrics_body_when_endpoint_is_reachable(
    tmp_path: pathlib.Path,
    network_runtime: NetworkRuntime,
) -> None:
    overlay_path = tmp_path / "50-observability.json5"
    compose_path = tmp_path / "compose.yaml"
    _write_observability_inputs(
        overlay_path,
        compose_path,
        otlp=network_runtime.tcp_listener(),
        metrics=network_runtime.http_listener(b""),
    )

    report = verify_observability(
        overlay_path=overlay_path,
        compose_path=compose_path,
        skip_runtime=False,
    )
    assert report.ok is True


def test_verify_channels_matches_repo_docs_and_guidance() -> None:
    report = verify_channels(
        overlay_path=REPO_ROOT / "platform/configs/openclaw/30-channels.json5",
        channels_doc_path=REPO_ROOT / "platform/docs/CHANNELS.md",
        telegram_guidance_path=REPO_ROOT / "platform/docs/channels/telegram.md",
        whatsapp_guidance_path=REPO_ROOT / "platform/docs/channels/whatsapp.md",
        allowlist_source_path=REPO_ROOT / "platform/configs/source-allowlists.example.yaml",
    )

    assert report.ok is True


def test_repo_aux_stack_keeps_litellm_rootfs_writable_for_prisma_sanity_check() -> None:
    compose = load_yaml(REPO_ROOT / "platform/compose/docker-compose.aux-stack.yaml")
    litellm_service = compose["services"]["litellm"]

    assert litellm_service.get("read_only") is not True


def test_repo_aux_stack_healthchecks_use_binaries_available_in_the_pinned_images() -> None:
    compose = load_yaml(REPO_ROOT / "platform/compose/docker-compose.aux-stack.yaml")

    litellm_test = compose["services"]["litellm"]["healthcheck"]["test"]
    qdrant_test = compose["services"]["qdrant"]["healthcheck"]["test"]
    neo4j_test = compose["services"]["neo4j"]["healthcheck"]["test"]

    assert litellm_test[:2] == ["CMD", "/usr/bin/python3"]
    assert "urllib.request.urlopen" in litellm_test[3]
    assert qdrant_test[:3] == ["CMD", "bash", "-lc"]
    assert "/dev/tcp/127.0.0.1/6333" in qdrant_test[3]
    assert neo4j_test[:3] == ["CMD", "bash", "-lc"]
    assert "/dev/tcp/127.0.0.1/7474" in neo4j_test[3]


def test_repo_aux_stack_neo4j_auth_uses_shared_varlock_credentials() -> None:
    compose_paths = (
        REPO_ROOT / "platform/compose/docker-compose.aux-stack.yaml",
        REPO_ROOT / "platform/compose/docker-compose.aux-stack.ci-hosted-macos.yaml",
    )

    expected_auth = (
        "${NEO4J_USERNAME:-neo4j}/"
        "${NEO4J_PASSWORD:?Set NEO4J_PASSWORD or use clawops varlock-env configure}"
    )
    expected_image = (
        "neo4j:5.26.23-community@"
        "sha256:40bf5ae9282213087e4d6036aab3ec443fe9c974d3dd4f14a11892c63157238f"
    )

    for compose_path in compose_paths:
        compose = load_yaml(compose_path)
        neo4j_env = compose["services"]["neo4j"]["environment"]
        neo4j_image = compose["services"]["neo4j"]["image"]
        assert neo4j_image == expected_image
        assert neo4j_env["NEO4J_AUTH"] == expected_auth
