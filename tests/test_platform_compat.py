"""Tests for host compatibility resolution."""

from __future__ import annotations

from clawops.platform_compat import (
    DARWIN_X64_LOCAL_RERANK_TORCH_CONSTRAINT,
    DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION,
    DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
    SUPPORTED_LOCAL_RERANK_TORCH_CONSTRAINT,
    HostPlatform,
    build_compatibility_record,
    detect_host_platform,
    normalize_architecture,
    normalize_os_name,
    resolve_hypermemory_local_rerank_torch_constraint,
    resolve_memory_plugin_lancedb_version,
    resolve_service_manager,
    supports_hypermemory_local_rerank,
)


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
    assert record["memory_plugin_lancedb_version"] == DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION
    assert record["memory_plugin_default_lancedb_version"] == DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION
    assert record["memory_plugin_override_required"] is True
    assert record["hypermemory_local_rerank_supported"] is True
    assert (
        record["hypermemory_local_rerank_torch_constraint"]
        == DARWIN_X64_LOCAL_RERANK_TORCH_CONSTRAINT
    )
    assert record["hypermemory_local_rerank_requires_http_fallback"] is False


def test_local_rerank_support_matrix_tracks_known_host_python_combinations() -> None:
    assert supports_hypermemory_local_rerank(HostPlatform("darwin", "arm64"), python_version="3.13")
    assert supports_hypermemory_local_rerank(HostPlatform("linux", "x86_64"), python_version="3.13")
    assert supports_hypermemory_local_rerank(HostPlatform("linux", "arm64"), python_version="3.12")
    assert supports_hypermemory_local_rerank(
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


def test_local_rerank_constraint_tracks_supported_host_specific_pins() -> None:
    assert (
        resolve_hypermemory_local_rerank_torch_constraint(
            HostPlatform("darwin", "x86_64"),
            python_version="3.12",
        )
        == DARWIN_X64_LOCAL_RERANK_TORCH_CONSTRAINT
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
