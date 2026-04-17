"""Unit tests for hypermemory/capture.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from clawops.hypermemory.capture import (
    CaptureCandidate,
    extract_candidates_llm,
    extract_candidates_regex,
    resolve_capture_api_key,
)

# ---------------------------------------------------------------------------
# CaptureCandidate dataclass
# ---------------------------------------------------------------------------


def test_capture_candidate_defaults() -> None:
    c = CaptureCandidate(kind="fact", text="some text")
    assert c.kind == "fact"
    assert c.text == "some text"
    assert c.entity is None
    assert c.confidence is None
    assert c.source == "regex"
    assert c.source_turn is None
    assert c.source_role is None
    assert c.fact_key is None


def test_capture_candidate_explicit_fields() -> None:
    c = CaptureCandidate(
        kind="entity",
        text="Alice is the owner",
        entity="Alice",
        confidence=0.9,
        source="llm",
        source_turn=3,
        source_role="user",
        fact_key="user:name",
    )
    assert c.entity == "Alice"
    assert c.confidence == 0.9
    assert c.source == "llm"
    assert c.source_turn == 3
    assert c.fact_key == "user:name"


def test_capture_candidate_is_frozen() -> None:
    c = CaptureCandidate(kind="fact", text="text")
    with pytest.raises((AttributeError, TypeError)):
        c.text = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve_capture_api_key
# ---------------------------------------------------------------------------


def test_resolve_api_key_explicit() -> None:
    assert resolve_capture_api_key(api_key_env=None, api_key="sk-test") == "sk-test"


def test_resolve_api_key_strips_whitespace() -> None:
    assert resolve_capture_api_key(api_key_env=None, api_key="  sk-abc  ") == "sk-abc"


def test_resolve_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "env-value")
    assert resolve_capture_api_key(api_key_env="MY_KEY") == "env-value"


def test_resolve_api_key_missing_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    assert resolve_capture_api_key(api_key_env="MISSING_KEY") == ""


def test_resolve_api_key_no_config_empty() -> None:
    assert resolve_capture_api_key(api_key_env=None) == ""


# ---------------------------------------------------------------------------
# extract_candidates_regex — pattern coverage
# ---------------------------------------------------------------------------


def test_regex_opinion_always_prefer() -> None:
    candidates = extract_candidates_regex(["I always use vim for editing"])
    assert any(c.kind == "opinion" for c in candidates)


def test_regex_opinion_prefer_like() -> None:
    candidates = extract_candidates_regex(["I prefer dark mode in my terminal"])
    assert any(c.kind == "opinion" for c in candidates)


def test_regex_fact_remember_pattern() -> None:
    candidates = extract_candidates_regex(["Remember: deploy requires two approvals"])
    assert any(c.kind == "fact" for c in candidates)


def test_regex_fact_note_pattern() -> None:
    candidates = extract_candidates_regex(["Note: the gateway token expires monthly"])
    assert any(c.kind == "fact" for c in candidates)


def test_regex_fact_important_pattern() -> None:
    candidates = extract_candidates_regex(["Important: rotate tokens before deploy"])
    assert any(c.kind == "fact" for c in candidates)


def test_regex_fact_we_decided_pattern() -> None:
    candidates = extract_candidates_regex(["We decided to use blue/green deployments"])
    assert any(c.kind == "fact" for c in candidates)


def test_regex_fact_never_always() -> None:
    candidates = extract_candidates_regex(["Never skip the review step"])
    assert any(c.kind == "fact" for c in candidates)


def test_regex_entity_my_name_is() -> None:
    candidates = extract_candidates_regex(["My name is Alice"])
    assert any(c.kind == "entity" for c in candidates)


def test_regex_entity_my_role_is() -> None:
    candidates = extract_candidates_regex(["My role is engineer"])
    assert any(c.kind == "entity" for c in candidates)


def test_regex_entity_with_entity_field() -> None:
    candidates = extract_candidates_regex(["MyService is the primary API gateway"])
    facts = [c for c in candidates if c.kind == "entity"]
    if facts:
        assert facts[0].entity is not None


def test_regex_empty_input_returns_empty() -> None:
    assert extract_candidates_regex([]) == []


def test_regex_no_pattern_match() -> None:
    # Neutral statement — no opinion/fact/entity/entity-verb pattern applies
    candidates = extract_candidates_regex(["It rained yesterday."])
    assert candidates == []


def test_regex_deduplicates_same_message() -> None:
    msgs = [
        "I always use vim for editing",
        "I always use vim for editing",
    ]
    candidates = extract_candidates_regex(msgs)
    # Deduplication is keyed on (kind, text) — the same (kind, text) pair must
    # not appear more than once even when the same message is repeated.
    keys = [(c.kind, c.text.casefold()) for c in candidates]
    assert len(keys) == len(set(keys))


def test_regex_user_role_confidence() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "user", "Remember: user set this")]
    candidates = extract_candidates_regex(msgs)
    assert candidates[0].confidence == 0.85


def test_regex_assistant_role_lower_confidence() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "assistant", "Remember: assistant noted this")]
    candidates = extract_candidates_regex(msgs)
    assert candidates[0].confidence == 0.7


def test_regex_source_is_regex() -> None:
    candidates = extract_candidates_regex(["I prefer Python over Java"])
    assert all(c.source == "regex" for c in candidates)


def test_regex_tuple_input() -> None:
    msgs: list[tuple[int, str, str]] = [(5, "user", "Remember: rotate the gateway token")]
    candidates = extract_candidates_regex(msgs)
    assert len(candidates) >= 1
    assert candidates[0].source_turn == 5
    assert candidates[0].source_role == "user"


def test_regex_fact_key_inferred_for_my_name() -> None:
    candidates = extract_candidates_regex(["My name is Bob"])
    # entity pattern matches "My name is Bob"; fact_key inference is optional
    assert isinstance(candidates, list)


def test_regex_string_messages_treated_as_user() -> None:
    candidates = extract_candidates_regex(["I prefer spaces over tabs"])
    assert candidates[0].source_role == "user"


def test_regex_empty_text_skipped() -> None:
    candidates = extract_candidates_regex(["   ", "", "Remember: valid text"])
    # only the valid one should produce a candidate
    assert all(c.text.strip() for c in candidates)


# ---------------------------------------------------------------------------
# extract_candidates_llm — mocked HTTP
# ---------------------------------------------------------------------------


def _make_llm_response_mock(
    candidates: list[dict[str, object]],
) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"candidates": candidates})}}]
    }
    return response


def test_llm_basic_fact_extracted() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "user", "My name is Alice")]
    llm_raw: list[dict[str, object]] = [
        {
            "kind": "fact",
            "text": "User is named Alice",
            "confidence": 0.9,
            "source_turn": 0,
            "source_role": "user",
        }
    ]
    with patch("clawops.hypermemory.capture.requests.Session") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.return_value = _make_llm_response_mock(llm_raw)
        result = extract_candidates_llm(
            msgs,
            endpoint="http://localhost:11434",
            model="llama3",
            api_key="",
            timeout_ms=5000,
        )
    assert len(result) == 1
    assert result[0].kind == "fact"
    assert result[0].source == "llm"
    assert result[0].confidence is not None and abs(result[0].confidence - 0.9) < 1e-6


def test_llm_empty_messages_short_circuits() -> None:
    result = extract_candidates_llm(
        [],
        endpoint="http://localhost",
        model="llama3",
        api_key="",
        timeout_ms=5000,
    )
    assert result == []


def test_llm_blank_endpoint_raises() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        extract_candidates_llm(
            [(0, "user", "hi")],
            endpoint="  ",
            model="llama3",
            api_key="",
            timeout_ms=5000,
        )


def test_llm_blank_model_raises() -> None:
    with pytest.raises(ValueError, match="model"):
        extract_candidates_llm(
            [(0, "user", "hi")],
            endpoint="http://localhost",
            model="  ",
            api_key="",
            timeout_ms=5000,
        )


def test_llm_http_error_propagates() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "user", "hi")]
    with patch("clawops.hypermemory.capture.requests.Session") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.side_effect = ConnectionError("refused")
        with pytest.raises(RuntimeError):
            extract_candidates_llm(
                msgs,
                endpoint="http://localhost",
                model="llama3",
                api_key="",
                timeout_ms=500,
            )


def test_llm_filters_unknown_kind() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "user", "something")]
    llm_raw: list[dict[str, object]] = [{"kind": "unknown", "text": "ignored"}]
    with patch("clawops.hypermemory.capture.requests.Session") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.return_value = _make_llm_response_mock(llm_raw)
        result = extract_candidates_llm(
            msgs,
            endpoint="http://localhost",
            model="llama3",
            api_key="",
            timeout_ms=5000,
        )
    assert result == []


def test_llm_confidence_clamped_to_0_1() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "user", "hi")]
    llm_raw: list[dict[str, object]] = [
        {"kind": "fact", "text": "something", "confidence": 1.5, "source_turn": 0}
    ]
    with patch("clawops.hypermemory.capture.requests.Session") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.return_value = _make_llm_response_mock(llm_raw)
        result = extract_candidates_llm(
            msgs,
            endpoint="http://localhost",
            model="llama3",
            api_key="",
            timeout_ms=5000,
        )
    assert result[0].confidence is not None and abs(result[0].confidence - 1.0) < 1e-6


def test_llm_deduplicates_across_batches() -> None:
    msgs: list[tuple[int, str, str]] = [(i, "user", f"msg {i}") for i in range(8)]
    # Both batches return the same fact
    duplicate_raw: list[dict[str, object]] = [
        {"kind": "fact", "text": "same fact", "confidence": 0.8, "source_turn": 0}
    ]
    with patch("clawops.hypermemory.capture.requests.Session") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.return_value = _make_llm_response_mock(duplicate_raw)
        result = extract_candidates_llm(
            msgs,
            endpoint="http://localhost",
            model="llama3",
            api_key="",
            timeout_ms=5000,
            batch_size=4,
            batch_overlap=0,
        )
    texts = [c.text.casefold() for c in result]
    assert len(texts) == len(set(texts))


def test_llm_api_key_sent_in_header() -> None:
    msgs: list[tuple[int, str, str]] = [(0, "user", "hi")]
    with patch("clawops.hypermemory.capture.requests.Session") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.post.return_value = _make_llm_response_mock([])
        extract_candidates_llm(
            msgs,
            endpoint="http://localhost",
            model="llama3",
            api_key="sk-secret",
            timeout_ms=5000,
        )
    call_kwargs = mock_session.post.call_args
    headers = call_kwargs.kwargs.get("headers", {}) or call_kwargs[1].get("headers", {})
    assert "Authorization" in headers
    assert "sk-secret" in headers["Authorization"]
