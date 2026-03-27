"""HTTP transport doubles for wrapper tests."""

from __future__ import annotations

from collections.abc import Sequence

import requests

from tests.plugins.infrastructure.context import TestContext


class FakeResponse:
    """Minimal response double for requests-based wrappers."""

    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        text: str = "ok",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self.headers = {} if headers is None else headers


def install_success_response(test_context: TestContext, calls: list[str]) -> None:
    """Install a one-shot successful HTTP response."""

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        calls.append("request")
        return FakeResponse(text="ok")

    test_context.patch.patch("clawops.wrappers.base.requests.request", new=_request)


def install_transport_error(
    test_context: TestContext,
    message: str,
    calls: list[str] | None = None,
) -> None:
    """Install a transport timeout failure."""

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        if calls is not None:
            calls.append("request")
        raise requests.Timeout(message)

    test_context.patch.patch("clawops.wrappers.base.requests.request", new=_request)


def install_status_sequence(
    test_context: TestContext,
    responses: Sequence[FakeResponse],
    calls: list[str],
) -> None:
    """Install a fixed sequence of HTTP responses."""

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        calls.append("request")
        return responses[len(calls) - 1]

    test_context.patch.patch("clawops.wrappers.base.requests.request", new=_request)
