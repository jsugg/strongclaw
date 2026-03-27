"""Unit tests for the tracked test context kernel."""

from __future__ import annotations

import logging

import pytest

from tests.utils.helpers.test_context import TestContext


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Cleanable:
    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


class _Deletable:
    def __init__(self) -> None:
        self.deleted = False

    def delete(self) -> None:
        self.deleted = True


def test_context_generates_unique_tid() -> None:
    assert TestContext().tid != TestContext().tid


def test_context_generates_unique_resource_prefix() -> None:
    assert TestContext().resource_prefix != TestContext().resource_prefix


def test_context_worker_id_defaults_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert TestContext().worker_id == "main"


def test_context_stores_nodeid() -> None:
    ctx = TestContext(nodeid="tests/example.py::test_case", test_name="test_case")
    assert ctx.nodeid == "tests/example.py::test_case"


def test_register_resource_stores_and_retrieves() -> None:
    ctx = TestContext()
    resource = object()

    ctx.register_resource("resource", resource, expect_cleanup=False)

    assert ctx.get_resource("resource") is resource


def test_register_resource_auto_detects_close_method() -> None:
    ctx = TestContext()
    resource = _Closable()

    ctx.register_resource("resource", resource)
    ctx.cleanup_all()

    assert resource.closed is True


def test_register_resource_auto_detects_cleanup_method() -> None:
    ctx = TestContext()
    resource = _Cleanable()

    ctx.register_resource("resource", resource)
    ctx.cleanup_all()

    assert resource.cleaned is True


def test_register_resource_auto_detects_delete_method() -> None:
    ctx = TestContext()
    resource = _Deletable()

    ctx.register_resource("resource", resource)
    ctx.cleanup_all()

    assert resource.deleted is True


def test_register_resource_prefers_explicit_cleanup_over_auto() -> None:
    ctx = TestContext()
    resource = _Closable()
    called: list[str] = []

    ctx.register_resource("resource", resource, cleanup=lambda: called.append("explicit"))
    ctx.cleanup_all()

    assert called == ["explicit"]
    assert resource.closed is False


def test_register_resource_with_expect_cleanup_false_skips_auto() -> None:
    ctx = TestContext()
    resource = _Closable()

    ctx.register_resource("resource", resource, expect_cleanup=False)

    assert ctx.audit_uncleaned() == []


def test_cleanup_all_runs_lifo_order() -> None:
    ctx = TestContext()
    calls: list[str] = []

    ctx.register_cleanup("first", lambda: calls.append("first"))
    ctx.register_cleanup("second", lambda: calls.append("second"))
    ctx.cleanup_all()

    assert calls == ["second", "first"]


def test_cleanup_all_collects_errors_without_aborting() -> None:
    ctx = TestContext()
    calls: list[str] = []
    ctx.register_cleanup("broken", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    ctx.register_cleanup("good", lambda: calls.append("good"))

    errors = ctx.cleanup_all()

    assert calls == ["good"]
    assert len(errors) == 1
    assert errors[0][0] == "broken"


def test_cleanup_all_is_idempotent() -> None:
    ctx = TestContext()
    calls: list[str] = []
    ctx.register_cleanup("resource", lambda: calls.append("clean"))

    ctx.cleanup_all()
    ctx.cleanup_all()

    assert calls == ["clean"]


def test_audit_uncleaned_returns_expected_but_not_cleaned() -> None:
    ctx = TestContext()
    ctx.register_resource("resource", _Closable())

    assert ctx.audit_uncleaned() == ["resource"]


def test_audit_uncleaned_returns_empty_when_all_cleaned() -> None:
    ctx = TestContext()
    ctx.register_resource("resource", _Closable())
    ctx.cleanup_all()

    assert ctx.audit_uncleaned() == []


def test_register_cleanup_standalone_action() -> None:
    ctx = TestContext()
    calls: list[str] = []
    ctx.register_cleanup("cleanup", lambda: calls.append("cleanup"))

    ctx.cleanup_all()

    assert calls == ["cleanup"]


def test_overwrite_resource_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    ctx = TestContext()
    caplog.set_level(logging.WARNING)

    ctx.register_resource("resource", object(), expect_cleanup=False)
    ctx.register_resource("resource", object(), expect_cleanup=False)

    assert "overwritten" in caplog.text


def test_notes_dict_available_for_diagnostics() -> None:
    ctx = TestContext()
    ctx.notes["scope"] = "diagnostic"
    assert ctx.notes["scope"] == "diagnostic"
