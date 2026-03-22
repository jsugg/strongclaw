"""Regression tests for deterministic hypermemory sparse encoding."""

from __future__ import annotations

from clawops.hypermemory.sparse import build_sparse_encoder


def test_sparse_encoder_is_deterministic_for_the_same_corpus() -> None:
    texts = [
        "Gateway token rollover requires a checklist.",
        "Alice owns the gateway deployment checklist.",
    ]

    first = build_sparse_encoder(texts)
    second = build_sparse_encoder(texts)

    assert first.fingerprint == second.fingerprint
    assert first.term_to_id == second.term_to_id
    assert first.encode_document(texts[0]) == second.encode_document(texts[0])


def test_sparse_encoder_ignores_empty_and_punctuation_only_content() -> None:
    encoder = build_sparse_encoder(["!!!", "   ", "Gateway token"])

    assert encoder.encode_document("!!!").is_empty is True
    assert encoder.encode_query("...").is_empty is True
    assert encoder.encode_document("Gateway token").is_empty is False


def test_sparse_encoder_fingerprint_changes_when_corpus_stats_change() -> None:
    baseline = build_sparse_encoder(["gateway token", "alice owns rollout"])
    changed = build_sparse_encoder(["gateway token", "alice owns rollout", "gateway gateway"])

    assert baseline.fingerprint != changed.fingerprint


def test_sparse_encoder_query_and_document_paths_share_normalization_rules() -> None:
    encoder = build_sparse_encoder(["Gateway-token recovery plan"])

    document_vector = encoder.encode_document("Gateway-token recovery plan")
    query_vector = encoder.encode_query("gateway token recovery plan")

    assert set(document_vector.indices) == set(query_vector.indices)
    assert document_vector.is_empty is False
    assert query_vector.is_empty is False
