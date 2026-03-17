"""Platform compatibility helpers for bootstrap and plugin install flows."""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform

DEFAULT_OPENCLAW_VERSION = "2026.3.13"
DEFAULT_ACPX_VERSION = "0.3.0"
DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION = "0.26.2"
DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION = "0.22.3"


@dataclasses.dataclass(frozen=True, slots=True)
class HostPlatform:
    """Normalized host platform identity."""

    os_name: str
    architecture: str


def normalize_os_name(value: str) -> str:
    """Normalize an OS name to the project-supported tokens."""
    lowered = value.strip().casefold()
    if lowered in {"darwin", "mac", "macos", "osx"}:
        return "darwin"
    if lowered == "linux":
        return "linux"
    return lowered


def normalize_architecture(value: str) -> str:
    """Normalize an architecture token to the project-supported values."""
    lowered = value.strip().casefold()
    if lowered in {"x86_64", "amd64"}:
        return "x86_64"
    if lowered in {"arm64", "aarch64"}:
        return "arm64"
    return lowered


def detect_host_platform(
    *, os_name: str | None = None, architecture: str | None = None
) -> HostPlatform:
    """Detect and normalize the current host platform."""
    resolved_os = normalize_os_name(platform.system() if os_name is None else os_name)
    resolved_arch = normalize_architecture(
        platform.machine() if architecture is None else architecture
    )
    return HostPlatform(os_name=resolved_os, architecture=resolved_arch)


def resolve_bootstrap_script(host: HostPlatform) -> str:
    """Select the bootstrap entrypoint for the detected host."""
    if host.os_name == "darwin":
        return "bootstrap_macos.sh"
    if host.os_name == "linux":
        return "bootstrap_linux.sh"
    raise ValueError(f"unsupported host OS for bootstrap: {host.os_name}")


def resolve_preflight_script(host: HostPlatform) -> str:
    """Select the preflight entrypoint for the detected host."""
    if host.os_name == "darwin":
        return "preflight_macos.sh"
    if host.os_name == "linux":
        return "preflight_linux.sh"
    raise ValueError(f"unsupported host OS for preflight: {host.os_name}")


def resolve_memory_plugin_lancedb_version(host: HostPlatform) -> str:
    """Return the compatible LanceDB version for the vendored memory plugin."""
    if host.os_name == "darwin" and host.architecture == "x86_64":
        return DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION
    return DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION


def build_compatibility_record(host: HostPlatform) -> dict[str, object]:
    """Return a JSON-safe compatibility description for the host."""
    resolved_lancedb = resolve_memory_plugin_lancedb_version(host)
    return {
        "host_os": host.os_name,
        "host_arch": host.architecture,
        "preflight_script": resolve_preflight_script(host),
        "bootstrap_script": resolve_bootstrap_script(host),
        "openclaw_version": DEFAULT_OPENCLAW_VERSION,
        "acpx_version": DEFAULT_ACPX_VERSION,
        "memory_plugin_default_lancedb_version": DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
        "memory_plugin_lancedb_version": resolved_lancedb,
        "memory_plugin_override_required": resolved_lancedb
        != DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for platform compatibility queries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--os")
    parser.add_argument("--arch")
    parser.add_argument(
        "--field",
        choices=(
            "host_os",
            "host_arch",
            "preflight_script",
            "bootstrap_script",
            "openclaw_version",
            "acpx_version",
            "memory_plugin_default_lancedb_version",
            "memory_plugin_lancedb_version",
            "memory_plugin_override_required",
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Print host compatibility information."""
    args = parse_args(argv)
    host = detect_host_platform(os_name=args.os, architecture=args.arch)
    payload = build_compatibility_record(host)
    if args.field is not None:
        value = payload[args.field]
        if isinstance(value, bool):
            print("true" if value else "false")
        else:
            print(value)
        return 0
    print(
        json.dumps(payload, indent=2, sort_keys=True)
        if args.json
        else json.dumps(payload, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
