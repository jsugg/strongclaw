"""Mock-or-real mode resolution for service-backed tests."""

from __future__ import annotations

import os
from typing import Literal, cast

import pytest

type ServiceMode = Literal["mock", "real"]

_VALID_MODES: frozenset[str] = frozenset({"mock", "real"})


def _coerce_mode(value: object) -> ServiceMode | None:
    """Return a validated service mode, or ``None`` for invalid values."""
    if isinstance(value, str) and value in _VALID_MODES:
        return cast(ServiceMode, value)
    return None


def resolve_service_mode(
    request: pytest.FixtureRequest,
    service: str,
    *,
    marker_name: str | None = None,
    default: ServiceMode = "mock",
) -> ServiceMode:
    """Resolve mock-or-real mode for a service-backed test."""
    mock_services = request.config.getoption("mock")
    if isinstance(mock_services, list) and service in mock_services:
        return "mock"

    env_mode = _coerce_mode(os.environ.get(f"{service.upper()}_TEST_MODE"))
    if env_mode is not None:
        return env_mode

    marker = request.node.get_closest_marker(marker_name or service)
    if marker is not None:
        marker_mode = _coerce_mode(marker.kwargs.get("mode"))
        if marker_mode is not None:
            return marker_mode

    return default


def register_mock_addoption(parser: pytest.Parser) -> None:
    """Register the repeatable ``--mock`` service override option."""
    parser.addoption(
        "--mock",
        action="append",
        default=[],
        dest="mock",
        metavar="SERVICE",
        help="Force mock mode for SERVICE (repeatable: --mock qdrant --mock litellm).",
    )
