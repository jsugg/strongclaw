"""Contracts for managed network runtime helpers."""

from __future__ import annotations

import socket

import pytest
import requests

from tests.utils.helpers.network_runtime import NetworkRuntime
from tests.utils.helpers.test_context import TestContext


def test_network_runtime_accepts_test_context() -> None:
    ctx = TestContext()
    runtime = NetworkRuntime(context=ctx)
    endpoint = runtime.tcp_listener()

    with socket.create_connection(endpoint, timeout=1.0):
        pass

    ctx.cleanup_all()

    with pytest.raises(OSError):
        socket.create_connection(endpoint, timeout=0.1)


def test_network_runtime_works_without_context() -> None:
    with NetworkRuntime() as runtime:
        host, port = runtime.http_listener(b"ok\n")
        response = requests.get(f"http://{host}:{port}", timeout=1.0)

    assert response.content == b"ok\n"
