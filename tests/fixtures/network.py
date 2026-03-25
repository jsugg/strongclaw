"""Pytest fixtures for local-network integration tests."""

from __future__ import annotations

import pytest

from tests.utils.helpers.network import (
    Endpoint,
    HttpServerFactory,
    ListenerFactory,
    disconnecting_listener,
    http_server,
    tcp_listener,
)


@pytest.fixture
def tcp_listener_factory() -> ListenerFactory:
    """Expose the TCP listener context manager as a fixture-bound factory."""
    return tcp_listener


@pytest.fixture
def disconnecting_listener_factory() -> ListenerFactory:
    """Expose the disconnecting TCP listener as a fixture-bound factory."""
    return disconnecting_listener


@pytest.fixture
def http_server_factory() -> HttpServerFactory:
    """Expose the static HTTP server context manager as a fixture-bound factory."""
    return http_server


__all__ = [
    "Endpoint",
    "HttpServerFactory",
    "ListenerFactory",
    "disconnecting_listener",
    "disconnecting_listener_factory",
    "http_server",
    "http_server_factory",
    "tcp_listener",
    "tcp_listener_factory",
]
