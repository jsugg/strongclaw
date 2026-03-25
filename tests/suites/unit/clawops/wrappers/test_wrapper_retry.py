"""HTTP transport and logging coverage for wrappers."""

from __future__ import annotations

import json
import pathlib

import pytest
import requests
from pytest import MonkeyPatch

from clawops.wrappers.base import HttpTimeouts, JsonHttpClient, RetryPolicy
from clawops.wrappers.github import add_labels
from clawops.wrappers.webhook import invoke_webhook
from tests.utils.helpers.wrappers import (
    SPECS,
    FakeResponse,
    WrapperSpec,
    build_context,
    configure_wrapper_environment,
    install_status_sequence,
    install_success_response,
    install_transport_error,
)


def test_json_http_client_retries_when_policy_allows(monkeypatch: MonkeyPatch) -> None:
    calls: list[str] = []

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        calls.append("request")
        if len(calls) == 1:
            raise requests.Timeout("transient timeout")
        return FakeResponse(text="ok")

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)
    monkeypatch.setenv("CLAWOPS_HTTP_RETRY_MODE", "safe")
    client = JsonHttpClient(timeout=5)

    outcome = client.post(
        "https://example.internal/hooks/deploy",
        headers={"Content-Type": "application/json"},
        json_body={"ok": True},
        retry_policy=RetryPolicy(
            name="safe-test",
            max_attempts=2,
            base_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )

    assert outcome.request_attempts == 2
    assert outcome.request_method == "POST"
    assert outcome.request_url == "https://example.internal/hooks/deploy"
    assert outcome.response.text == "ok"
    assert calls == ["request", "request"]


def test_retry_mode_off_disables_safe_label_retries(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, SPECS[2], require_approval=False)
    configure_wrapper_environment(SPECS[2], monkeypatch)
    monkeypatch.setenv("CLAWOPS_HTTP_RETRY_MODE", "off")
    calls: list[str] = []
    install_transport_error(monkeypatch, "simulated timeout", calls)

    result = add_labels(
        ctx=ctx,
        repo="example/repo",
        issue_number=123,
        labels=["needs-review"],
        scope="test",
        trust_zone="automation",
    )

    assert result["ok"] is False
    assert result["retryable"] is False
    assert result["request_attempts"] == 1
    assert calls == ["request"]

    persisted = journal.get(str(result["op_id"]))
    assert persisted.result_error_retryable == 0
    assert persisted.result_request_attempts == 1


def test_github_labels_retry_retryable_http_status_then_succeeds(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, SPECS[2], require_approval=False)
    configure_wrapper_environment(SPECS[2], monkeypatch)
    monkeypatch.setenv("CLAWOPS_HTTP_RETRY_MODE", "safe")
    calls: list[str] = []
    install_status_sequence(
        monkeypatch,
        responses=[
            FakeResponse(
                ok=False,
                status_code=503,
                text="busy",
                headers={"Retry-After": "3", "X-GitHub-Request-Id": "req-123"},
            ),
            FakeResponse(ok=True, status_code=200, text="ok"),
        ],
        calls=calls,
    )

    result = add_labels(
        ctx=ctx,
        repo="example/repo",
        issue_number=123,
        labels=["needs-review"],
        scope="test",
        trust_zone="automation",
    )
    replayed = add_labels(
        ctx=ctx,
        repo="example/repo",
        issue_number=123,
        labels=["needs-review"],
        scope="test",
        trust_zone="automation",
    )

    assert result["ok"] is True
    assert result["request_attempts"] == 2
    assert result["request_id"] == "req-123"
    assert result["retry_after_seconds"] == 3.0
    assert calls == ["request", "request"]
    assert replayed == result

    persisted = journal.get(str(result["op_id"]))
    assert persisted.status == "succeeded"
    assert persisted.result_request_attempts == 2
    assert persisted.result_request_id == "req-123"
    assert persisted.result_retry_after_seconds == 3.0


def test_github_labels_retry_timeout_then_succeeds(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, SPECS[2], require_approval=False)
    configure_wrapper_environment(SPECS[2], monkeypatch)
    monkeypatch.setenv("CLAWOPS_HTTP_RETRY_MODE", "safe")
    calls: list[str] = []

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        calls.append("request")
        if len(calls) == 1:
            raise requests.Timeout("transient timeout")
        return FakeResponse(
            ok=True,
            status_code=200,
            text="ok",
            headers={"X-GitHub-Request-Id": "req-success"},
        )

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)

    result = add_labels(
        ctx=ctx,
        repo="example/repo",
        issue_number=123,
        labels=["needs-review"],
        scope="test",
        trust_zone="automation",
    )
    replayed = add_labels(
        ctx=ctx,
        repo="example/repo",
        issue_number=123,
        labels=["needs-review"],
        scope="test",
        trust_zone="automation",
    )

    assert result["ok"] is True
    assert result["request_attempts"] == 2
    assert result["request_id"] == "req-success"
    assert "retry_after_seconds" not in result
    assert calls == ["request", "request"]
    assert replayed == result

    persisted = journal.get(str(result["op_id"]))
    assert persisted.status == "succeeded"
    assert persisted.result_request_attempts == 2
    assert persisted.result_request_id == "req-success"
    assert persisted.result_retry_after_seconds is None


@pytest.mark.parametrize(
    "spec",
    [SPECS[0], SPECS[1], SPECS[3]],
    ids=["webhook", "github-comment", "github-merge"],
)
def test_non_retry_wrappers_fail_once_on_retryable_http_status(
    spec: WrapperSpec,
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ctx, journal = build_context(tmp_path, spec, require_approval=False)
    configure_wrapper_environment(spec, monkeypatch)
    calls: list[str] = []
    install_status_sequence(
        monkeypatch,
        responses=[
            FakeResponse(
                ok=False,
                status_code=503,
                text="busy",
                headers={"Retry-After": "5", "X-Request-Id": "req-503"},
            )
        ],
        calls=calls,
    )

    first = spec.invoke(ctx, spec.allowed_input)
    second = spec.invoke(ctx, spec.allowed_input)

    assert first["ok"] is False
    assert first["accepted"] is True
    assert first["executed"] is True
    assert first["status"] == "failed"
    assert first["error_type"] == "http_status"
    assert first["retryable"] is False
    assert first["request_attempts"] == 1
    assert first["request_id"] == "req-503"
    assert first["retry_after_seconds"] == 5.0
    assert first["error"]["request_id"] == "req-503"
    assert first["error"]["retry_after_seconds"] == 5.0
    assert calls == ["request"]
    assert second == first

    persisted = journal.get(str(first["op_id"]))
    assert persisted.status == "failed"
    assert persisted.result_request_attempts == 1
    assert persisted.result_request_id == "req-503"
    assert persisted.result_retry_after_seconds == 5.0


def test_wrapper_emits_structured_log_when_enabled(
    tmp_path: pathlib.Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx, _ = build_context(tmp_path, SPECS[0], require_approval=False)
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    calls: list[str] = []
    install_success_response(monkeypatch, calls)

    result = invoke_webhook(
        ctx=ctx,
        url="https://example.internal/hooks/deploy",
        payload_body={"ok": True},
        scope="test",
        trust_zone="automation",
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err.strip())
    assert result["ok"] is True
    assert payload["event"] == "clawops.wrapper.execute"
    assert payload["kind"] == "webhook_post"
    assert payload["op_id"] == result["op_id"]
    assert payload["request_attempts"] == 1
    assert payload["status_code"] == 200
    assert calls == ["request"]


def test_json_http_client_supports_split_timeouts(monkeypatch: MonkeyPatch) -> None:
    captured_timeouts: list[object] = []

    def _request(*args: object, **kwargs: object) -> FakeResponse:
        del args
        captured_timeouts.append(kwargs["timeout"])
        return FakeResponse(text="ok")

    monkeypatch.setattr("clawops.wrappers.base.requests.request", _request)
    client = JsonHttpClient(timeout=HttpTimeouts(connect_seconds=2.5, read_seconds=7.5))

    outcome = client.post(
        "https://example.internal/hooks/deploy",
        headers={"Content-Type": "application/json"},
        json_body={"ok": True},
    )

    assert outcome.response.text == "ok"
    assert captured_timeouts == [(2.5, 7.5)]
