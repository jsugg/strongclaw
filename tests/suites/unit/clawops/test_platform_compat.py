"""Tests for host compatibility resolution."""

from __future__ import annotations

import pytest

from clawops.platform_compat import (
    DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION,
    DEFAULT_MANAGED_PROJECT_PYTHON_VERSION,
    DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
    SUPPORTED_LOCAL_RERANK_TORCH_CONSTRAINT,
    SUPPORTED_NODE_RANGE,
    HostPlatform,
    NodeVersion,
    build_compatibility_record,
    detect_host_platform,
    node_version_supported,
    normalize_architecture,
    normalize_os_name,
    parse_node_version,
    resolve_hypermemory_local_rerank_torch_constraint,
    resolve_memory_plugin_lancedb_version,
    resolve_preferred_project_python_version,
    resolve_service_manager,
    supports_hypermemory_local_rerank,
)


def test_supported_node_range_renders_the_npm_engine_syntax() -> None:
    assert SUPPORTED_NODE_RANGE == ">=24.22.1 <25 || >=26.12.1"


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ((22, 23, 5), False),
        ((23, 11, 1), False),
        ((24, 22, 0), False),
        ((24, 22, 1), True),
        ((24, 22, 3), True),
        ((25, 9, 0), False),
        ((26, 12, 0), False),
        ((26, 12, 1), True),
        ((27, 0, 0), True),
        ((20, 99, 99), False),
    ],
)
def test_node_version_supported_follows_each_release_line_floor(
    version: NodeVersion, supported: bool
) -> None:
    assert node_version_supported(version) is supported


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v24.22.1\n", (24, 22, 1)),
        ("26.12.1", (26, 12, 1)),
        ("v25.9.0+build.1", (25, 9, 0)),
        ("v24.22", None),
        ("v26.0.0-nightly2026", None),
        ("", None),
    ],
)
def test_parse_node_version_accepts_release_versions_only(
    text: str, expected: NodeVersion | None
) -> None:
    assert parse_node_version(text) == expected


def test_normalize_platform_tokens() -> None:
    assert normalize_os_name("Darwin") == "darwin"
    assert normalize_os_name("macOS") == "darwin"
    assert normalize_os_name("Linux") == "linux"
    assert normalize_architecture("amd64") == "x86_64"
    assert normalize_architecture("aarch64") == "arm64"


def test_detect_host_platform_normalizes_inputs() -> None:
    host = detect_host_platform(os_name="Darwin", architecture="amd64")

    assert host == HostPlatform(os_name="darwin", architecture="x86_64")


def test_service_manager_matches_supported_operating_systems() -> None:
    assert resolve_service_manager(HostPlatform("darwin", "arm64")) == "launchd"
    assert resolve_service_manager(HostPlatform("linux", "x86_64")) == "systemd"


def test_memory_plugin_lancedb_version_uses_intel_mac_fallback_only() -> None:
    assert (
        resolve_memory_plugin_lancedb_version(HostPlatform("darwin", "x86_64"))
        == DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION
    )
    assert (
        resolve_memory_plugin_lancedb_version(HostPlatform("darwin", "arm64"))
        == DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION
    )
    assert (
        resolve_memory_plugin_lancedb_version(HostPlatform("linux", "x86_64"))
        == DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION
    )


def test_build_compatibility_record_reports_when_override_is_required() -> None:
    record = build_compatibility_record(HostPlatform("darwin", "x86_64"), python_version="3.12")

    assert record["service_manager"] == "launchd"
    assert record["python_version"] == "3.12"
    assert record["preferred_project_python_version"] == DEFAULT_MANAGED_PROJECT_PYTHON_VERSION
    assert record["memory_plugin_lancedb_version"] == DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION
    assert record["memory_plugin_default_lancedb_version"] == DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION
    assert record["memory_plugin_override_required"] is True
    assert record["hypermemory_local_rerank_supported"] is False
    assert record["hypermemory_local_rerank_torch_constraint"] is None
    assert record["hypermemory_local_rerank_requires_http_fallback"] is True


def test_local_rerank_support_matrix_tracks_known_host_python_combinations() -> None:
    assert supports_hypermemory_local_rerank(HostPlatform("darwin", "arm64"), python_version="3.13")
    assert supports_hypermemory_local_rerank(HostPlatform("linux", "x86_64"), python_version="3.13")
    assert supports_hypermemory_local_rerank(HostPlatform("linux", "arm64"), python_version="3.12")
    assert not supports_hypermemory_local_rerank(
        HostPlatform("darwin", "x86_64"),
        python_version="3.12",
    )
    assert not supports_hypermemory_local_rerank(
        HostPlatform("darwin", "x86_64"),
        python_version="3.13",
    )
    assert not supports_hypermemory_local_rerank(
        HostPlatform("linux", "armv7l"),
        python_version="3.12",
    )


def test_managed_install_prefers_python_312_on_supported_hosts() -> None:
    assert (
        resolve_preferred_project_python_version(HostPlatform("darwin", "x86_64"))
        == DEFAULT_MANAGED_PROJECT_PYTHON_VERSION
    )
    assert (
        resolve_preferred_project_python_version(HostPlatform("darwin", "arm64"))
        == DEFAULT_MANAGED_PROJECT_PYTHON_VERSION
    )
    assert (
        resolve_preferred_project_python_version(HostPlatform("linux", "x86_64"))
        == DEFAULT_MANAGED_PROJECT_PYTHON_VERSION
    )
    assert (
        resolve_preferred_project_python_version(HostPlatform("linux", "arm64"))
        == DEFAULT_MANAGED_PROJECT_PYTHON_VERSION
    )
    assert resolve_preferred_project_python_version(HostPlatform("windows", "x86_64")) is None


def test_local_rerank_constraint_tracks_supported_host_specific_pins() -> None:
    assert (
        resolve_hypermemory_local_rerank_torch_constraint(
            HostPlatform("darwin", "x86_64"),
            python_version="3.12",
        )
        is None
    )
    assert (
        resolve_hypermemory_local_rerank_torch_constraint(
            HostPlatform("darwin", "arm64"),
            python_version="3.13",
        )
        == SUPPORTED_LOCAL_RERANK_TORCH_CONSTRAINT
    )
    assert (
        resolve_hypermemory_local_rerank_torch_constraint(
            HostPlatform("linux", "x86_64"),
            python_version="3.13",
        )
        == SUPPORTED_LOCAL_RERANK_TORCH_CONSTRAINT
    )
    assert (
        resolve_hypermemory_local_rerank_torch_constraint(
            HostPlatform("darwin", "x86_64"),
            python_version="3.13",
        )
        is None
    )
    assert (
        resolve_hypermemory_local_rerank_torch_constraint(
            HostPlatform("linux", "x86_64"),
            python_version="3.14",
        )
        is None
    )
