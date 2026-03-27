"""Tests for the backend registry execution contract."""

from __future__ import annotations

from clawops.backend_registry import registry_contract, resolve_backend


def test_backend_registry_exposes_permission_and_output_defaults() -> None:
    codex = resolve_backend("codex")
    contract = registry_contract()
    codex_contract = next(item for item in contract if item["name"] == "codex")

    assert codex.default_permission_mode == "approve-reads"
    assert codex.supports_permission_mode("approve-all") is True
    assert codex.supports_permission_mode("deny-all") is True
    assert codex.default_output_format == "text"
    assert codex.supports_output_format("ndjson") is True
    assert codex_contract["supported_permission_modes"] == [
        "approve-all",
        "approve-reads",
        "deny-all",
    ]
    assert codex_contract["default_permission_mode"] == "approve-reads"
    assert codex_contract["supported_output_formats"] == ["text", "json", "ndjson"]
    assert codex_contract["default_output_format"] == "text"
