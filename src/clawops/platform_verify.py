"""Verify sidecar, observability, and channel platform contracts."""

from __future__ import annotations

import argparse
import dataclasses
import http.client
import json
import pathlib
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Final, cast

from clawops.allowlist_sync import load_source, render_fragment
from clawops.common import dump_json, load_text, load_yaml
from clawops.process_runner import run_command
from clawops.typed_values import as_mapping

DEFAULT_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]


@dataclasses.dataclass(slots=True, frozen=True)
class Check:
    """Single verification result."""

    name: str
    ok: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        """Convert the check to a JSON-safe dictionary."""
        return {"name": self.name, "ok": self.ok, "message": self.message}


@dataclasses.dataclass(slots=True, frozen=True)
class PublishedPort:
    """Normalized port publication contract."""

    host: str
    host_port: int
    container_port: int


@dataclasses.dataclass(slots=True, frozen=True)
class VerificationReport:
    """Collection of related verification checks."""

    name: str
    checks: Sequence[Check]

    @property
    def ok(self) -> bool:
        """Return True when every check passed."""
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        """Convert the report into JSON-safe primitives."""
        return {
            "name": self.name,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


def _ok(name: str, message: str) -> Check:
    """Create a successful check."""
    return Check(name=name, ok=True, message=message)


def _fail(name: str, message: str) -> Check:
    """Create a failing check."""
    return Check(name=name, ok=False, message=message)


def _load_mapping_yaml(path: pathlib.Path) -> dict[str, object]:
    """Load a YAML file and require a mapping root."""
    value = load_yaml(path)
    return dict(as_mapping(value, path=str(path)))


def _load_mapping_json(path: pathlib.Path) -> dict[str, object]:
    """Load a JSON overlay and require a mapping root."""
    value = json.loads(load_text(path))
    return dict(as_mapping(value, path=str(path)))


def _service_definition(
    compose: Mapping[str, object],
    service_name: str,
) -> Mapping[str, object] | None:
    """Return a compose service definition when present."""
    services = compose.get("services")
    if not isinstance(services, Mapping):
        return None
    services_mapping = cast(Mapping[str, object], services)
    service = services_mapping.get(service_name)
    if not isinstance(service, Mapping):
        return None
    return cast(Mapping[str, object], service)


def _parse_port_mapping(raw_value: object) -> PublishedPort | None:
    """Parse a docker-compose string port mapping."""
    if not isinstance(raw_value, str):
        return None
    parts = raw_value.split(":")
    if len(parts) != 3:
        return None
    host, host_port_text, container_port_text = parts
    try:
        host_port = int(host_port_text.split("/", 1)[0])
        container_port = int(container_port_text.split("/", 1)[0])
    except ValueError:
        return None
    return PublishedPort(host=host, host_port=host_port, container_port=container_port)


def _find_published_port(
    service: Mapping[str, object], container_port: int
) -> PublishedPort | None:
    """Find the published host port for a service container port."""
    ports = service.get("ports")
    if not isinstance(ports, list):
        return None
    for raw_value in cast(list[object], ports):
        published = _parse_port_mapping(raw_value)
        if published is not None and published.container_port == container_port:
            return published
    return None


def _is_loopback_host(host: str) -> bool:
    """Return True for loopback-only listeners."""
    return host in {"127.0.0.1", "localhost", "[::1]", "::1"}


def _check_required_service(
    compose: Mapping[str, object],
    *,
    service_name: str,
    container_port: int,
    require_healthcheck: bool,
) -> tuple[list[Check], PublishedPort | None]:
    """Validate a compose service contract."""
    service = _service_definition(compose, service_name)
    if service is None:
        return [_fail(service_name, f"missing compose service: {service_name}")], None

    published = _find_published_port(service, container_port)
    if published is None:
        return [
            _fail(
                service_name,
                f"missing published port for container port {container_port}",
            )
        ], None

    checks = [
        (
            _ok(
                f"{service_name}-port",
                f"{service_name} publishes {published.host}:{published.host_port}->{container_port}",
            )
            if _is_loopback_host(published.host)
            else _fail(
                f"{service_name}-port",
                f"{service_name} must bind loopback-only, found {published.host}:{published.host_port}",
            )
        )
    ]

    has_healthcheck = isinstance(service.get("healthcheck"), Mapping)
    if require_healthcheck:
        checks.append(
            _ok(f"{service_name}-healthcheck", "healthcheck configured")
            if has_healthcheck
            else _fail(f"{service_name}-healthcheck", "missing compose healthcheck")
        )
    return checks, published


def _check_tcp_endpoint(name: str, host: str, port: int) -> Check:
    """Probe a TCP listener."""
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return _ok(name, f"tcp reachability confirmed for {host}:{port}")
    except OSError as exc:
        return _fail(name, f"tcp reachability failed for {host}:{port}: {exc}")


def _check_http_endpoint(name: str, url: str, *, require_body: bool = False) -> Check:
    """Probe an HTTP endpoint."""
    request = urllib.request.Request(url, method="GET")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                body = response.read(4096).decode("utf-8", errors="replace")
                status = response.getcode()
        except (http.client.HTTPException, OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5)
                continue
            return _fail(name, f"http reachability failed for {url}: {exc}")
        if status < 200 or status >= 300:
            return _fail(name, f"http endpoint returned status {status}: {url}")
        if require_body and not body.strip():
            return _fail(name, f"http endpoint returned an empty body: {url}")
        return _ok(name, f"http reachability confirmed for {url}")

    detail = "unknown HTTP probe failure" if last_error is None else str(last_error)
    return _fail(name, f"http reachability failed for {url}: {detail}")


def _listener_addresses(port: int) -> list[str]:
    """Collect listener addresses for one TCP port."""
    commands = (
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "n"],
        ["ss", "-H", "-ltn", f"( sport = :{port} )"],
        ["netstat", "-an"],
    )
    for command in commands:
        result = run_command(command, timeout_seconds=10)
        if not result.ok:
            continue
        if command[0] == "lsof":
            return [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]
        if command[0] == "ss":
            return [line.split()[-1] for line in result.stdout.splitlines() if line.split()]
        return [
            line.split()[-1]
            for line in result.stdout.splitlines()
            if "LISTEN" in line and re.search(rf"[.:]{port}\b", line)
        ]
    return []


def _check_loopback_ports(ports: Sequence[int]) -> Check:
    """Verify that discovered listeners stay loopback-only."""
    failures: list[str] = []
    for port in ports:
        listeners = sorted(set(_listener_addresses(port)))
        if not listeners:
            continue
        non_loopback = [
            listener for listener in listeners if not _is_loopback_host(listener.split(":")[0])
        ]
        if non_loopback:
            failures.append(f"port {port}: {', '.join(non_loopback)}")
    if failures:
        return _fail("loopback-probe", "; ".join(failures))
    return _ok("loopback-probe", "runtime listeners remain loopback-only")


def verify_sidecars(
    *,
    compose_path: pathlib.Path,
    skip_runtime: bool = False,
) -> VerificationReport:
    """Verify sidecar compose contracts and runtime reachability."""
    compose = _load_mapping_yaml(compose_path)
    checks: list[Check] = []

    postgres_checks, postgres_port = _check_required_service(
        compose,
        service_name="postgres",
        container_port=5432,
        require_healthcheck=True,
    )
    litellm_checks, litellm_port = _check_required_service(
        compose,
        service_name="litellm",
        container_port=4000,
        require_healthcheck=True,
    )
    otlp_checks, otlp_port = _check_required_service(
        compose,
        service_name="otel-collector",
        container_port=4318,
        require_healthcheck=False,
    )
    metrics_checks, metrics_port = _check_required_service(
        compose,
        service_name="otel-collector",
        container_port=9464,
        require_healthcheck=False,
    )
    qdrant_checks, qdrant_port = _check_required_service(
        compose,
        service_name="qdrant",
        container_port=6333,
        require_healthcheck=True,
    )
    checks.extend(postgres_checks)
    checks.extend(litellm_checks)
    checks.extend(otlp_checks)
    checks.extend(metrics_checks)
    checks.extend(qdrant_checks)

    if skip_runtime:
        checks.append(_ok("runtime-probes", "runtime probes skipped"))
        return VerificationReport(name="sidecars", checks=checks)

    if postgres_port is not None:
        checks.append(
            _check_tcp_endpoint("postgres-runtime", postgres_port.host, postgres_port.host_port)
        )
    if litellm_port is not None:
        checks.append(
            _check_http_endpoint(
                "litellm-runtime",
                f"http://{litellm_port.host}:{litellm_port.host_port}/health/liveliness",
            )
        )
    if otlp_port is not None:
        checks.append(_check_tcp_endpoint("otlp-runtime", otlp_port.host, otlp_port.host_port))
    if metrics_port is not None:
        checks.append(
            _check_http_endpoint(
                "otel-metrics-runtime",
                f"http://{metrics_port.host}:{metrics_port.host_port}/metrics",
            )
        )
    if qdrant_port is not None:
        checks.append(
            _check_http_endpoint(
                "qdrant-runtime",
                f"http://{qdrant_port.host}:{qdrant_port.host_port}/healthz",
            )
        )
    discovered_ports = [
        published.host_port
        for published in (postgres_port, litellm_port, otlp_port, metrics_port, qdrant_port)
        if published is not None
    ]
    checks.append(_check_loopback_ports(discovered_ports))
    return VerificationReport(name="sidecars", checks=checks)


def verify_observability(
    *,
    overlay_path: pathlib.Path,
    compose_path: pathlib.Path | None = None,
    skip_runtime: bool = False,
) -> VerificationReport:
    """Verify observability overlay semantics and collector reachability."""
    overlay = _load_mapping_json(overlay_path)
    diagnostics = overlay.get("diagnostics")
    logging = overlay.get("logging")
    plugins = overlay.get("plugins")
    checks: list[Check] = []

    if not isinstance(diagnostics, Mapping):
        return VerificationReport(
            name="observability",
            checks=[_fail("diagnostics", f"missing diagnostics block in {overlay_path}")],
        )
    if not isinstance(logging, Mapping):
        return VerificationReport(
            name="observability",
            checks=[_fail("logging", f"missing logging block in {overlay_path}")],
        )
    if not isinstance(plugins, Mapping):
        return VerificationReport(
            name="observability",
            checks=[_fail("plugins", f"missing plugins block in {overlay_path}")],
        )
    diagnostics_mapping = cast(Mapping[str, object], diagnostics)
    logging_mapping = cast(Mapping[str, object], logging)
    plugins_mapping = cast(Mapping[str, object], plugins)

    otel = diagnostics_mapping.get("otel")
    if not isinstance(otel, Mapping):
        return VerificationReport(
            name="observability",
            checks=[_fail("otel", f"missing diagnostics.otel block in {overlay_path}")],
        )
    otel_mapping = cast(Mapping[str, object], otel)

    endpoint = otel_mapping.get("endpoint")
    if not isinstance(endpoint, str):
        return VerificationReport(
            name="observability",
            checks=[_fail("otel-endpoint", "diagnostics.otel.endpoint must be a string")],
        )
    parsed_endpoint = urllib.parse.urlparse(endpoint)
    endpoint_host = parsed_endpoint.hostname
    endpoint_port = parsed_endpoint.port

    checks.append(
        _ok("diagnostics-enabled", "diagnostics overlay enabled")
        if diagnostics_mapping.get("enabled") is True
        else _fail("diagnostics-enabled", "diagnostics.enabled must be true")
    )
    checks.append(
        _ok("otel-enabled", "OTel plugin enabled")
        if otel_mapping.get("enabled") is True
        else _fail("otel-enabled", "diagnostics.otel.enabled must be true")
    )
    checks.append(
        _ok("otel-protocol", "OTLP protocol uses http/protobuf")
        if otel_mapping.get("protocol") == "http/protobuf"
        else _fail("otel-protocol", "diagnostics.otel.protocol must be http/protobuf")
    )
    checks.append(
        _ok("otel-signals", "metrics and traces enabled")
        if otel_mapping.get("metrics") is True and otel_mapping.get("traces") is True
        else _fail("otel-signals", "diagnostics.otel.metrics and traces must both be true")
    )
    checks.append(
        _ok("logging-redaction", "logging redaction remains enabled")
        if logging_mapping.get("redactSensitive") == "tools"
        and isinstance(logging_mapping.get("redactPatterns"), list)
        else _fail("logging-redaction", "logging redaction contract is incomplete")
    )
    allow_plugins = plugins_mapping.get("allow")
    plugin_entries = plugins_mapping.get("entries")
    diagnostics_entry = (
        cast(Mapping[str, object], plugin_entries).get("diagnostics-otel")
        if isinstance(plugin_entries, Mapping)
        else None
    )
    checks.append(
        _ok("diagnostics-plugin", "diagnostics-otel plugin remains enabled")
        if isinstance(allow_plugins, list)
        and "diagnostics-otel" in allow_plugins
        and isinstance(diagnostics_entry, Mapping)
        and cast(Mapping[str, object], diagnostics_entry).get("enabled") is True
        else _fail(
            "diagnostics-plugin", "diagnostics-otel plugin must stay allowlisted and enabled"
        )
    )

    if endpoint_host is None or endpoint_port is None:
        checks.append(_fail("otel-endpoint", f"invalid OTLP endpoint: {endpoint}"))
        return VerificationReport(name="observability", checks=checks)

    checks.append(
        _ok("otel-endpoint", f"OTLP endpoint targets loopback at {endpoint_host}:{endpoint_port}")
        if _is_loopback_host(endpoint_host)
        else _fail("otel-endpoint", f"OTLP endpoint must stay loopback-only: {endpoint}")
    )

    metrics_port = 9464
    if compose_path is not None:
        compose = _load_mapping_yaml(compose_path)
        otlp_checks, otlp_mapping = _check_required_service(
            compose,
            service_name="otel-collector",
            container_port=4318,
            require_healthcheck=False,
        )
        metrics_checks, metrics_mapping = _check_required_service(
            compose,
            service_name="otel-collector",
            container_port=9464,
            require_healthcheck=False,
        )
        checks.extend(otlp_checks)
        checks.extend(metrics_checks)
        if otlp_mapping is not None and endpoint_port != otlp_mapping.host_port:
            checks.append(
                _fail(
                    "otel-endpoint-port",
                    f"overlay endpoint port {endpoint_port} does not match compose port {otlp_mapping.host_port}",
                )
            )
        if metrics_mapping is not None:
            metrics_port = metrics_mapping.host_port

    if skip_runtime:
        checks.append(_ok("runtime-probes", "runtime probes skipped"))
        return VerificationReport(name="observability", checks=checks)

    checks.append(_check_tcp_endpoint("otlp-runtime", endpoint_host, endpoint_port))
    checks.append(
        _check_http_endpoint(
            "otel-metrics-runtime",
            f"http://{endpoint_host}:{metrics_port}/metrics",
        )
    )
    return VerificationReport(name="observability", checks=checks)


def verify_channels(
    *,
    overlay_path: pathlib.Path,
    channels_doc_path: pathlib.Path,
    telegram_guidance_path: pathlib.Path,
    whatsapp_guidance_path: pathlib.Path,
    allowlist_source_path: pathlib.Path,
) -> VerificationReport:
    """Verify channel overlays, docs, and allowlist rendering parity."""
    overlay = _load_mapping_json(overlay_path)
    channels = overlay.get("channels")
    if not isinstance(channels, Mapping):
        return VerificationReport(
            name="channels",
            checks=[_fail("channels", f"missing channels block in {overlay_path}")],
        )
    channels_mapping = cast(Mapping[str, object], channels)

    telegram = channels_mapping.get("telegram")
    whatsapp = channels_mapping.get("whatsapp")
    defaults = channels_mapping.get("defaults")
    checks: list[Check] = []
    if not isinstance(telegram, Mapping):
        checks.append(_fail("telegram", "missing channels.telegram block"))
    if not isinstance(whatsapp, Mapping):
        checks.append(_fail("whatsapp", "missing channels.whatsapp block"))
    if not isinstance(defaults, Mapping):
        checks.append(_fail("defaults", "missing channels.defaults block"))
    if checks:
        return VerificationReport(name="channels", checks=checks)

    telegram_mapping = cast(Mapping[str, object], telegram)
    whatsapp_mapping = cast(Mapping[str, object], whatsapp)
    defaults_mapping = cast(Mapping[str, object], defaults)
    checks.extend(
        [
            (
                _ok("telegram-dm-policy", "Telegram stays pairing-first")
                if telegram_mapping.get("dmPolicy") == "pairing"
                else _fail("telegram-dm-policy", "Telegram dmPolicy must remain pairing")
            ),
            (
                _ok("telegram-group-policy", "Telegram groups stay allowlisted")
                if telegram_mapping.get("groupPolicy") == "allowlist"
                else _fail("telegram-group-policy", "Telegram groupPolicy must remain allowlist")
            ),
            (
                _ok("telegram-allowlist", "Telegram allowFrom entries are configured")
                if isinstance(telegram_mapping.get("allowFrom"), list)
                and bool(telegram_mapping.get("allowFrom"))
                else _fail("telegram-allowlist", "Telegram allowFrom must be a non-empty list")
            ),
            (
                _ok("whatsapp-dm-policy", "WhatsApp stays pairing-first")
                if whatsapp_mapping.get("dmPolicy") == "pairing"
                else _fail("whatsapp-dm-policy", "WhatsApp dmPolicy must remain pairing")
            ),
            (
                _ok("whatsapp-group-policy", "WhatsApp groups stay allowlisted")
                if whatsapp_mapping.get("groupPolicy") == "allowlist"
                else _fail("whatsapp-group-policy", "WhatsApp groupPolicy must remain allowlist")
            ),
            (
                _ok("default-group-policy", "Default channel group policy remains allowlist")
                if defaults_mapping.get("groupPolicy") == "allowlist"
                else _fail(
                    "default-group-policy", "channels.defaults.groupPolicy must remain allowlist"
                )
            ),
        ]
    )

    docs_text = load_text(channels_doc_path)
    telegram_guidance_text = load_text(telegram_guidance_path)
    whatsapp_guidance_text = load_text(whatsapp_guidance_path)
    checks.extend(
        [
            (
                _ok("channel-docs-pairing", "Channel docs preserve pairing-first rollout guidance")
                if "approve first DM pairing" in docs_text and "durable allowlist" in docs_text
                else _fail(
                    "channel-docs-pairing", "Channel docs drifted from pairing-first guidance"
                )
            ),
            (
                _ok(
                    "channel-docs-allowlists",
                    "Channel docs still mention clawops allowlist rendering",
                )
                if "clawops allowlists" in docs_text
                else _fail(
                    "channel-docs-allowlists", "Channel docs must reference clawops allowlists"
                )
            ),
            (
                _ok(
                    "telegram-guidance-pairing",
                    "Telegram guidance preserves pairing approval guidance",
                )
                if "openclaw pairing approve telegram <CODE>" in telegram_guidance_text
                else _fail(
                    "telegram-guidance-pairing",
                    "Telegram guidance must surface pairing approval guidance",
                )
            ),
            (
                _ok(
                    "whatsapp-guidance-pairing",
                    "WhatsApp guidance preserves pairing-first guidance",
                )
                if "pairing" in whatsapp_guidance_text and "allowlist" in whatsapp_guidance_text
                else _fail(
                    "whatsapp-guidance-pairing",
                    "WhatsApp guidance drifted from pairing-first guidance",
                )
            ),
        ]
    )

    rendered = render_fragment(load_source(allowlist_source_path))
    rendered_channels = rendered["channels"]
    checks.extend(
        [
            (
                _ok(
                    "rendered-telegram-allowlist",
                    "Allowlist rendering normalizes Telegram sender IDs",
                )
                if rendered_channels["telegram"]["allowFrom"] == ["tg:12345678"]
                else _fail(
                    "rendered-telegram-allowlist",
                    "Expected sample Telegram allowlist to normalize to tg:12345678",
                )
            ),
            (
                _ok(
                    "rendered-whatsapp-allowlist",
                    "Allowlist rendering preserves WhatsApp E.164 IDs",
                )
                if rendered_channels["whatsapp"]["allowFrom"] == ["+5511999999999"]
                else _fail(
                    "rendered-whatsapp-allowlist",
                    "Expected sample WhatsApp allowlist to remain +5511999999999",
                )
            ),
        ]
    )
    return VerificationReport(name="channels", checks=checks)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for platform verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=pathlib.Path, default=DEFAULT_REPO_ROOT)
    subparsers = parser.add_subparsers(dest="target", required=True)

    sidecars = subparsers.add_parser("sidecars")
    sidecars.add_argument(
        "--repo-root", dest="subcommand_repo_root", type=pathlib.Path, default=None
    )
    sidecars.add_argument("--compose-file", type=pathlib.Path, default=None)
    sidecars.add_argument("--skip-runtime", action="store_true")

    observability = subparsers.add_parser("observability")
    observability.add_argument(
        "--repo-root",
        dest="subcommand_repo_root",
        type=pathlib.Path,
        default=None,
    )
    observability.add_argument("--overlay", type=pathlib.Path, default=None)
    observability.add_argument("--compose-file", type=pathlib.Path, default=None)
    observability.add_argument("--skip-runtime", action="store_true")

    channels = subparsers.add_parser("channels")
    channels.add_argument(
        "--repo-root", dest="subcommand_repo_root", type=pathlib.Path, default=None
    )
    channels.add_argument("--overlay", type=pathlib.Path, default=None)
    channels.add_argument("--doc", type=pathlib.Path, default=None)
    channels.add_argument("--telegram-guidance", type=pathlib.Path, default=None)
    channels.add_argument("--whatsapp-guidance", type=pathlib.Path, default=None)
    channels.add_argument("--allowlist-source", type=pathlib.Path, default=None)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the selected platform verification target."""
    args = parse_args(argv)
    repo_root_argument = (
        args.subcommand_repo_root
        if isinstance(getattr(args, "subcommand_repo_root", None), pathlib.Path)
        else args.repo_root
    )
    repo_root = (
        repo_root_argument.resolve()
        if repo_root_argument is not None
        else DEFAULT_REPO_ROOT.resolve()
    )

    if args.target == "sidecars":
        report = verify_sidecars(
            compose_path=(
                args.compose_file.resolve()
                if args.compose_file is not None
                else repo_root / "platform/compose/docker-compose.aux-stack.yaml"
            ),
            skip_runtime=bool(args.skip_runtime),
        )
    elif args.target == "observability":
        report = verify_observability(
            overlay_path=(
                args.overlay.resolve()
                if args.overlay is not None
                else repo_root / "platform/configs/openclaw/50-observability.json5"
            ),
            compose_path=(
                args.compose_file.resolve()
                if args.compose_file is not None
                else repo_root / "platform/compose/docker-compose.aux-stack.yaml"
            ),
            skip_runtime=bool(args.skip_runtime),
        )
    else:
        report = verify_channels(
            overlay_path=(
                args.overlay.resolve()
                if args.overlay is not None
                else repo_root / "platform/configs/openclaw/30-channels.json5"
            ),
            channels_doc_path=(
                args.doc.resolve()
                if args.doc is not None
                else repo_root / "platform/docs/CHANNELS.md"
            ),
            telegram_guidance_path=(
                args.telegram_guidance.resolve()
                if args.telegram_guidance is not None
                else repo_root / "platform/docs/channels/telegram.md"
            ),
            whatsapp_guidance_path=(
                args.whatsapp_guidance.resolve()
                if args.whatsapp_guidance is not None
                else repo_root / "platform/docs/channels/whatsapp.md"
            ),
            allowlist_source_path=(
                args.allowlist_source.resolve()
                if args.allowlist_source is not None
                else repo_root / "platform/configs/source-allowlists.example.yaml"
            ),
        )

    print(dump_json(report.to_dict()).rstrip())
    return 0 if report.ok else 1
