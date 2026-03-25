"""Tests for platform verification commands."""

from __future__ import annotations

import pathlib

from clawops.common import load_yaml, write_text, write_yaml
from clawops.platform_verify import verify_channels, verify_observability, verify_sidecars
from tests.utils.helpers.network import HttpServerFactory, ListenerFactory
from tests.utils.helpers.repo import REPO_ROOT


def test_verify_sidecars_supports_runtime_probes(
    tmp_path: pathlib.Path,
    tcp_listener_factory: ListenerFactory,
    http_server_factory: HttpServerFactory,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    with tcp_listener_factory() as (postgres_host, postgres_port):
        with http_server_factory(b"alive\n") as (litellm_host, litellm_port):
            with tcp_listener_factory() as (otlp_host, otlp_port):
                with http_server_factory(b"otel metrics\n") as (metrics_host, metrics_port):
                    with http_server_factory(b"ok\n") as (qdrant_host, qdrant_port):
                        write_yaml(
                            compose_path,
                            {
                                "services": {
                                    "postgres": {
                                        "ports": [f"{postgres_host}:{postgres_port}:5432"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                    "litellm": {
                                        "ports": [f"{litellm_host}:{litellm_port}:4000"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                    "otel-collector": {
                                        "ports": [
                                            f"{otlp_host}:{otlp_port}:4318",
                                            f"{metrics_host}:{metrics_port}:9464",
                                        ]
                                    },
                                    "qdrant": {
                                        "ports": [f"{qdrant_host}:{qdrant_port}:6333"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                }
                            },
                        )

                        report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
                        assert report.ok is True


def test_verify_sidecars_reports_http_disconnects_without_crashing(
    tmp_path: pathlib.Path,
    tcp_listener_factory: ListenerFactory,
    disconnecting_listener_factory: ListenerFactory,
    http_server_factory: HttpServerFactory,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    with tcp_listener_factory() as (postgres_host, postgres_port):
        with disconnecting_listener_factory() as (litellm_host, litellm_port):
            with tcp_listener_factory() as (otlp_host, otlp_port):
                with http_server_factory(b"otel metrics\n") as (metrics_host, metrics_port):
                    with http_server_factory(b"ok\n") as (qdrant_host, qdrant_port):
                        write_yaml(
                            compose_path,
                            {
                                "services": {
                                    "postgres": {
                                        "ports": [f"{postgres_host}:{postgres_port}:5432"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                    "litellm": {
                                        "ports": [f"{litellm_host}:{litellm_port}:4000"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                    "otel-collector": {
                                        "ports": [
                                            f"{otlp_host}:{otlp_port}:4318",
                                            f"{metrics_host}:{metrics_port}:9464",
                                        ]
                                    },
                                    "qdrant": {
                                        "ports": [f"{qdrant_host}:{qdrant_port}:6333"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                }
                            },
                        )

                        report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
                        assert report.ok is False
                        litellm_check = next(
                            check for check in report.checks if check.name == "litellm-runtime"
                        )
                        assert litellm_check.ok is False
                        assert "http reachability failed" in litellm_check.message


def test_verify_sidecars_reports_qdrant_runtime_failures(
    tmp_path: pathlib.Path,
    tcp_listener_factory: ListenerFactory,
    disconnecting_listener_factory: ListenerFactory,
    http_server_factory: HttpServerFactory,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    with tcp_listener_factory() as (postgres_host, postgres_port):
        with http_server_factory(b"alive\n") as (litellm_host, litellm_port):
            with tcp_listener_factory() as (otlp_host, otlp_port):
                with http_server_factory(b"otel metrics\n") as (metrics_host, metrics_port):
                    with disconnecting_listener_factory() as (qdrant_host, qdrant_port):
                        write_yaml(
                            compose_path,
                            {
                                "services": {
                                    "postgres": {
                                        "ports": [f"{postgres_host}:{postgres_port}:5432"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                    "litellm": {
                                        "ports": [f"{litellm_host}:{litellm_port}:4000"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                    "otel-collector": {
                                        "ports": [
                                            f"{otlp_host}:{otlp_port}:4318",
                                            f"{metrics_host}:{metrics_port}:9464",
                                        ]
                                    },
                                    "qdrant": {
                                        "ports": [f"{qdrant_host}:{qdrant_port}:6333"],
                                        "healthcheck": {"test": ["CMD", "true"]},
                                    },
                                }
                            },
                        )

                        report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
                        assert report.ok is False
                        qdrant_check = next(
                            check for check in report.checks if check.name == "qdrant-runtime"
                        )
                        assert qdrant_check.ok is False
                        assert "http reachability failed" in qdrant_check.message


def test_verify_observability_supports_runtime_probes(
    tmp_path: pathlib.Path,
    tcp_listener_factory: ListenerFactory,
    http_server_factory: HttpServerFactory,
) -> None:
    overlay_path = tmp_path / "50-observability.json5"
    compose_path = tmp_path / "compose.yaml"
    with tcp_listener_factory() as (otlp_host, otlp_port):
        with http_server_factory(b"otel metrics\n") as (metrics_host, metrics_port):
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
                                f"{otlp_host}:{otlp_port}:4318",
                                f"{metrics_host}:{metrics_port}:9464",
                            ]
                        }
                    }
                },
            )

            report = verify_observability(
                overlay_path=overlay_path,
                compose_path=compose_path,
                skip_runtime=False,
            )
            assert report.ok is True


def test_verify_observability_accepts_empty_metrics_body_when_endpoint_is_reachable(
    tmp_path: pathlib.Path,
    tcp_listener_factory: ListenerFactory,
    http_server_factory: HttpServerFactory,
) -> None:
    overlay_path = tmp_path / "50-observability.json5"
    compose_path = tmp_path / "compose.yaml"
    with tcp_listener_factory() as (otlp_host, otlp_port):
        with http_server_factory(b"") as (metrics_host, metrics_port):
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
                                f"{otlp_host}:{otlp_port}:4318",
                                f"{metrics_host}:{metrics_port}:9464",
                            ]
                        }
                    }
                },
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

    assert litellm_test[:2] == ["CMD", "/usr/bin/python3"]
    assert "urllib.request.urlopen" in litellm_test[3]
    assert qdrant_test[:3] == ["CMD", "bash", "-lc"]
    assert "/dev/tcp/127.0.0.1/6333" in qdrant_test[3]
