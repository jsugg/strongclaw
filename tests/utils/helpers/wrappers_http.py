"""HTTP transport doubles for wrapper tests."""

from __future__ import annotations

from collections.abc import Sequence

import requests
from pytest import MonkeyPatch


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


def install_success_response(monkeypatch: MonkeyPatch, calls: list[str]) -> None:
    """Install a one-shot successful HTTP response."""

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        calls.append("request")
        return FakeResponse(text="ok")

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)


def install_transport_error(
    monkeypatch: MonkeyPatch,
    message: str,
    calls: list[str] | None = None,
) -> None:
    """Install a transport timeout failure."""

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        if calls is not None:
            calls.append("request")
        raise requests.Timeout(message)

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)


def install_status_sequence(
    monkeypatch: MonkeyPatch,
    responses: Sequence[FakeResponse],
    calls: list[str],
) -> None:
    """Install a fixed sequence of HTTP responses."""

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        calls.append("request")
        return responses[len(calls) - 1]

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)
