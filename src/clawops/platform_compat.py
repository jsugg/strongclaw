"""Platform compatibility helpers for host metadata and plugin install flows."""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys

DEFAULT_OPENCLAW_VERSION = "2026.3.13"
DEFAULT_ACPX_VERSION = "0.3.0"
DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION = "0.26.2"
DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION = "0.22.3"
DEFAULT_MANAGED_PROJECT_PYTHON_VERSION = "3.12"
SUPPORTED_LOCAL_RERANK_TORCH_CONSTRAINT = "torch==2.8.0"
DARWIN_X64_LOCAL_RERANK_TORCH_CONSTRAINT = "torch==2.2.2"
_SUPPORTED_LOCAL_RERANK_PYTHON_VERSIONS = {(3, 12), (3, 13)}


@dataclasses.dataclass(frozen=True, slots=True)
class HostPlatform:
    """Normalized host platform identity."""

    os_name: str
    architecture: str


def _normalize_python_version(value: str) -> tuple[int, int]:
    """Normalize a Python version string to a major/minor tuple."""
    parts = value.strip().split(".")
    if len(parts) < 2:
        raise ValueError(f"python version must include major.minor: {value!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as err:
        raise ValueError(f"python version must be numeric: {value!r}") from err


def _python_version_text(version: tuple[int, int]) -> str:
    """Return a normalized major.minor version string."""
    return f"{version[0]}.{version[1]}"


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


def resolve_service_manager(host: HostPlatform) -> str:
    """Return the native service manager for the detected host."""
    if host.os_name == "darwin":
        return "launchd"
    if host.os_name == "linux":
        return "systemd"
    raise ValueError(f"unsupported host OS for service management: {host.os_name}")


def resolve_memory_plugin_lancedb_version(host: HostPlatform) -> str:
    """Return the compatible LanceDB version for the vendored memory plugin."""
    if host.os_name == "darwin" and host.architecture == "x86_64":
        return DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION
    return DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION


def resolve_preferred_project_python_version(host: HostPlatform) -> str | None:
    """Return the managed Python version to prefer for install/setup flows."""
    if host.os_name in {"darwin", "linux"}:
        return DEFAULT_MANAGED_PROJECT_PYTHON_VERSION
    return None


def supports_hypermemory_local_rerank(
    host: HostPlatform, *, python_version: str | None = None
) -> bool:
    """Return whether the local rerank dependency should be installed."""
    resolved_python = _normalize_python_version(
        python_version
        if python_version is not None
        else _python_version_text((sys.version_info.major, sys.version_info.minor))
    )
    if resolved_python not in _SUPPORTED_LOCAL_RERANK_PYTHON_VERSIONS:
        return False
    if host.os_name == "darwin" and host.architecture == "x86_64":
        return resolved_python == (3, 12)
    if host.os_name == "darwin" and host.architecture == "arm64":
        return True
    if host.os_name == "linux" and host.architecture in {"x86_64", "arm64"}:
        return True
    return False


def resolve_hypermemory_local_rerank_torch_constraint(
    host: HostPlatform, *, python_version: str | None = None
) -> str | None:
    """Return any host-specific torch constraint for local reranking."""
    if not supports_hypermemory_local_rerank(host, python_version=python_version):
        return None
    if host.os_name == "darwin" and host.architecture == "x86_64":
        return DARWIN_X64_LOCAL_RERANK_TORCH_CONSTRAINT
    return SUPPORTED_LOCAL_RERANK_TORCH_CONSTRAINT


def build_compatibility_record(
    host: HostPlatform, *, python_version: str | None = None
) -> dict[str, object]:
    """Return a JSON-safe compatibility description for the host."""
    resolved_lancedb = resolve_memory_plugin_lancedb_version(host)
    resolved_python = _python_version_text(
        _normalize_python_version(
            python_version
            if python_version is not None
            else _python_version_text((sys.version_info.major, sys.version_info.minor))
        )
    )
    local_rerank_supported = supports_hypermemory_local_rerank(host, python_version=resolved_python)
    return {
        "host_os": host.os_name,
        "host_arch": host.architecture,
        "python_version": resolved_python,
        "service_manager": resolve_service_manager(host),
        "openclaw_version": DEFAULT_OPENCLAW_VERSION,
        "acpx_version": DEFAULT_ACPX_VERSION,
        "preferred_project_python_version": resolve_preferred_project_python_version(host),
        "memory_plugin_default_lancedb_version": DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
        "memory_plugin_lancedb_version": resolved_lancedb,
        "memory_plugin_override_required": resolved_lancedb
        != DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
        "hypermemory_local_rerank_supported": local_rerank_supported,
        "hypermemory_local_rerank_torch_constraint": (
            resolve_hypermemory_local_rerank_torch_constraint(host, python_version=resolved_python)
        ),
        "hypermemory_local_rerank_requires_http_fallback": not local_rerank_supported,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for platform compatibility queries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--os")
    parser.add_argument("--arch")
    parser.add_argument("--python-version")
    parser.add_argument(
        "--field",
        choices=(
            "host_os",
            "host_arch",
            "python_version",
            "service_manager",
            "openclaw_version",
            "acpx_version",
            "preferred_project_python_version",
            "memory_plugin_default_lancedb_version",
            "memory_plugin_lancedb_version",
            "memory_plugin_override_required",
            "hypermemory_local_rerank_supported",
            "hypermemory_local_rerank_torch_constraint",
            "hypermemory_local_rerank_requires_http_fallback",
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Print host compatibility information."""
    args = parse_args(argv)
    host = detect_host_platform(os_name=args.os, architecture=args.arch)
    payload = build_compatibility_record(host, python_version=args.python_version)
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
