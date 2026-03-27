"""Compatibility re-export for infrastructure service-mode helpers."""

from __future__ import annotations

from tests.plugins.infrastructure.mode import (
    ServiceMode,
    register_mock_addoption,
    resolve_service_mode,
)

__all__ = ["ServiceMode", "register_mock_addoption", "resolve_service_mode"]
