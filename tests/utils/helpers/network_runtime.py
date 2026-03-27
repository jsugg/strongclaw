"""Managed network listener helpers for integration tests."""

from __future__ import annotations

from contextlib import ExitStack
from typing import ContextManager

from tests.plugins.infrastructure.types import RuntimeTestContext
from tests.utils.helpers.network import (
    Endpoint,
    disconnecting_listener,
    http_server,
    tcp_listener,
)


class NetworkRuntime:
    """Manage listener lifecycles through ``TestContext`` or a local exit stack."""

    def __init__(self, context: RuntimeTestContext | None = None) -> None:
        self._context = context
        self._exit_stack = ExitStack()
        self._resource_index = 0

    def __enter__(self) -> NetworkRuntime:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def close(self) -> None:
        """Close all runtime-managed listeners that were not bound to ``TestContext``."""
        self._exit_stack.close()

    def _enter_listener(self, name: str, manager: ContextManager[Endpoint]) -> Endpoint:
        self._resource_index += 1
        resource_name = f"{name}:{self._resource_index}"
        if self._context is None:
            return self._exit_stack.enter_context(manager)

        def _cleanup() -> None:
            manager.__exit__(None, None, None)

        endpoint = manager.__enter__()
        self._context.register_resource(
            resource_name,
            endpoint,
            cleanup=_cleanup,
        )
        return endpoint

    def tcp_listener(self) -> Endpoint:
        """Start a TCP listener and return its bound endpoint."""
        return self._enter_listener("network:tcp", tcp_listener())

    def http_listener(self, body: bytes) -> Endpoint:
        """Start an HTTP listener that always returns the provided body."""
        return self._enter_listener("network:http", http_server(body))

    def disconnecting_listener(self) -> Endpoint:
        """Start a TCP listener that immediately disconnects clients."""
        return self._enter_listener("network:disconnect", disconnecting_listener())
