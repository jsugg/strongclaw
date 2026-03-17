"""Tests for host compatibility resolution."""

from __future__ import annotations

from clawops.platform_compat import (
    DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION,
    DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION,
    HostPlatform,
    build_compatibility_record,
    detect_host_platform,
    normalize_architecture,
    normalize_os_name,
    resolve_bootstrap_script,
    resolve_memory_plugin_lancedb_version,
    resolve_preflight_script,
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


def test_bootstrap_script_matches_supported_operating_systems() -> None:
    assert resolve_bootstrap_script(HostPlatform("darwin", "arm64")) == "bootstrap_macos.sh"
    assert resolve_bootstrap_script(HostPlatform("linux", "x86_64")) == "bootstrap_linux.sh"


def test_preflight_script_matches_supported_operating_systems() -> None:
    assert resolve_preflight_script(HostPlatform("darwin", "arm64")) == "preflight_macos.sh"
    assert resolve_preflight_script(HostPlatform("linux", "x86_64")) == "preflight_linux.sh"


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
    record = build_compatibility_record(HostPlatform("darwin", "x86_64"))

    assert record["preflight_script"] == "preflight_macos.sh"
    assert record["bootstrap_script"] == "bootstrap_macos.sh"
    assert record["memory_plugin_lancedb_version"] == DARWIN_X64_MEMORY_PLUGIN_LANCEDB_VERSION
    assert record["memory_plugin_default_lancedb_version"] == DEFAULT_MEMORY_PLUGIN_LANCEDB_VERSION
    assert record["memory_plugin_override_required"] is True
