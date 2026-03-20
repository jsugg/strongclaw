"""Tests for platform verification commands."""

from __future__ import annotations

import contextlib
import http.server
import pathlib
import socket
import socketserver
import threading

from clawops.common import load_yaml, write_text, write_yaml
from clawops.platform_verify import verify_channels, verify_observability, verify_sidecars


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    response_body = b"ok\n"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return None


@contextlib.contextmanager
def _tcp_listener() -> tuple[str, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()
    stop_event = threading.Event()

    def _accept_loop() -> None:
        while not stop_event.is_set():
            try:
                listener.settimeout(0.1)
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                pass

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        yield host, int(port)
    finally:
        stop_event.set()
        listener.close()
        thread.join(timeout=1)


@contextlib.contextmanager
def _disconnecting_listener() -> tuple[str, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()
    stop_event = threading.Event()

    def _accept_loop() -> None:
        while not stop_event.is_set():
            try:
                listener.settimeout(0.1)
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            conn.close()

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        yield host, int(port)
    finally:
        stop_event.set()
        listener.close()
        thread.join(timeout=1)


@contextlib.contextmanager
def _http_server(body: bytes) -> tuple[str, int]:
    class Handler(_StaticHandler):
        response_body = body

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_verify_sidecars_supports_runtime_probes(tmp_path: pathlib.Path) -> None:
    compose_path = tmp_path / "compose.yaml"
    with _tcp_listener() as (postgres_host, postgres_port):
        with _http_server(b"alive\n") as (litellm_host, litellm_port):
            with _tcp_listener() as (otlp_host, otlp_port):
                with _http_server(b"otel metrics\n") as (metrics_host, metrics_port):
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
                            }
                        },
                    )

                    report = verify_sidecars(compose_path=compose_path, skip_runtime=False)
                    assert report.ok is True


def test_verify_sidecars_reports_http_disconnects_without_crashing(
    tmp_path: pathlib.Path,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    with _tcp_listener() as (postgres_host, postgres_port):
        with _disconnecting_listener() as (litellm_host, litellm_port):
            with _tcp_listener() as (otlp_host, otlp_port):
                with _http_server(b"otel metrics\n") as (metrics_host, metrics_port):
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


def test_verify_observability_supports_runtime_probes(tmp_path: pathlib.Path) -> None:
    overlay_path = tmp_path / "50-observability.json5"
    compose_path = tmp_path / "compose.yaml"
    with _tcp_listener() as (otlp_host, otlp_port):
        with _http_server(b"otel metrics\n") as (metrics_host, metrics_port):
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
) -> None:
    overlay_path = tmp_path / "50-observability.json5"
    compose_path = tmp_path / "compose.yaml"
    with _tcp_listener() as (otlp_host, otlp_port):
        with _http_server(b"") as (metrics_host, metrics_port):
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


def test_verify_channels_matches_repo_docs_and_scripts() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    report = verify_channels(
        overlay_path=repo_root / "platform/configs/openclaw/30-channels.json5",
        channels_doc_path=repo_root / "platform/docs/CHANNELS.md",
        telegram_script_path=repo_root / "scripts/bootstrap/enable_telegram.sh",
        whatsapp_script_path=repo_root / "scripts/bootstrap/enable_whatsapp.sh",
        allowlist_source_path=repo_root / "platform/configs/source-allowlists.example.yaml",
    )

    assert report.ok is True


def test_repo_aux_stack_keeps_litellm_rootfs_writable_for_prisma_sanity_check() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    compose = load_yaml(repo_root / "platform/compose/docker-compose.aux-stack.yaml")
    litellm_service = compose["services"]["litellm"]

    assert litellm_service.get("read_only") is not True


def test_repo_aux_stack_healthchecks_use_binaries_available_in_the_pinned_images() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    compose = load_yaml(repo_root / "platform/compose/docker-compose.aux-stack.yaml")

    litellm_test = compose["services"]["litellm"]["healthcheck"]["test"]
    qdrant_test = compose["services"]["qdrant"]["healthcheck"]["test"]

    assert litellm_test[:2] == ["CMD", "/usr/bin/python3"]
    assert "urllib.request.urlopen" in litellm_test[3]
    assert qdrant_test[:3] == ["CMD", "bash", "-lc"]
    assert "/dev/tcp/127.0.0.1/6333" in qdrant_test[3]
