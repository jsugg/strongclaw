"""Unit tests for service mode resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.utils.helpers.mode import register_mock_addoption, resolve_service_mode


@dataclass(slots=True)
class _DummyConfig:
    options: dict[str, object] = field(default_factory=dict)

    def getoption(self, name: str, default: object = None) -> object:
        return self.options.get(name, default)


@dataclass(slots=True)
class _DummyNode:
    marker: pytest.Mark | None = None

    def get_closest_marker(self, name: str) -> pytest.Mark | None:
        if self.marker is None or self.marker.name != name:
            return None
        return self.marker


@dataclass(slots=True)
class _DummyRequest:
    config: _DummyConfig
    node: _DummyNode


class _ParserRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, object]]] = []

    def addoption(self, *args: Any, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


def _request(
    *,
    mock_services: list[str] | None = None,
    marker: pytest.Mark | None = None,
) -> _DummyRequest:
    return _DummyRequest(
        config=_DummyConfig(options={"mock": [] if mock_services is None else mock_services}),
        node=_DummyNode(marker=marker),
    )


def test_resolve_defaults_to_mock() -> None:
    assert resolve_service_mode(_request(), "qdrant") == "mock"


def test_resolve_cli_overrides_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_TEST_MODE", "real")
    request = _request(
        mock_services=["qdrant"],
        marker=pytest.mark.qdrant(mode="real").mark,
    )

    assert resolve_service_mode(request, "qdrant") == "mock"


def test_resolve_env_overrides_marker_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_TEST_MODE", "real")
    request = _request(marker=pytest.mark.qdrant(mode="mock").mark)

    assert resolve_service_mode(request, "qdrant") == "real"


def test_resolve_marker_overrides_default() -> None:
    request = _request(marker=pytest.mark.qdrant(mode="real").mark)

    assert resolve_service_mode(request, "qdrant") == "real"


def test_resolve_ignores_invalid_env_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_TEST_MODE", "invalid")
    request = _request(marker=pytest.mark.qdrant(mode="real").mark)

    assert resolve_service_mode(request, "qdrant") == "real"


def test_resolve_custom_marker_name() -> None:
    request = _request(marker=pytest.mark.network_local(mode="real").mark)

    assert resolve_service_mode(request, "service", marker_name="network_local") == "real"


def test_pytest_addoption_registers_mock_flag() -> None:
    parser = _ParserRecorder()

    register_mock_addoption(parser)  # type: ignore[arg-type]

    args, kwargs = parser.calls[0]
    assert args == ("--mock",)
    assert kwargs["dest"] == "mock"
    assert kwargs["action"] == "append"
