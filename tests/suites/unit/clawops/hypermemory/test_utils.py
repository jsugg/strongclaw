"""Unit tests for hypermemory/utils.py."""

from __future__ import annotations

import hashlib

from clawops.hypermemory.utils import (
    normalize_text,
    normalized_retrieval_text,
    point_id,
    sha256,
    slugify,
)

# ---------------------------------------------------------------------------
# sha256
# ---------------------------------------------------------------------------


def test_sha256_known_digest() -> None:
    expected = hashlib.sha256(b"hello").hexdigest()
    assert sha256("hello") == expected


def test_sha256_empty_string() -> None:
    expected = hashlib.sha256(b"").hexdigest()
    assert sha256("") == expected


def test_sha256_unicode() -> None:
    result = sha256("café")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_sha256_is_deterministic() -> None:
    assert sha256("stable") == sha256("stable")


def test_sha256_different_inputs_produce_different_digests() -> None:
    assert sha256("abc") != sha256("def")


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_simple_path() -> None:
    assert slugify("memory/facts.md") == "memory-facts-md"


def test_slugify_spaces() -> None:
    assert slugify("hello world") == "hello-world"


def test_slugify_special_chars() -> None:
    assert slugify("foo!@#bar") == "foo-bar"


def test_slugify_uppercase() -> None:
    assert slugify("CamelCase") == "camelcase"


def test_slugify_leading_trailing_separators() -> None:
    result = slugify("  --test--  ")
    assert result == "test"


def test_slugify_consecutive_non_alnum() -> None:
    assert slugify("a---b") == "a-b"


def test_slugify_empty_string() -> None:
    assert slugify("") == "entity"


def test_slugify_all_non_alnum() -> None:
    assert slugify("!!!---") == "entity"


def test_slugify_unicode_letters() -> None:
    result = slugify("café")
    # non-ASCII treated as non-alnum, letters stripped
    assert isinstance(result, str)
    assert len(result) > 0


def test_slugify_numeric_only() -> None:
    assert slugify("123") == "123"


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


def test_normalize_text_splits_into_tokens() -> None:
    result = normalize_text("Hello World")
    assert result == ("hello", "world")


def test_normalize_text_strips_punctuation() -> None:
    result = normalize_text("foo, bar! baz.")
    assert result == ("foo", "bar", "baz")


def test_normalize_text_empty_string() -> None:
    assert normalize_text("") == ()


def test_normalize_text_whitespace_only() -> None:
    assert normalize_text("   ") == ()


def test_normalize_text_returns_tuple() -> None:
    assert isinstance(normalize_text("test"), tuple)


def test_normalize_text_unicode_alphanum_preserved() -> None:
    tokens = normalize_text("abc123")
    assert "abc123" in tokens


def test_normalize_text_collapses_repeated_separators() -> None:
    result = normalize_text("a   b  c")
    assert result == ("a", "b", "c")


# ---------------------------------------------------------------------------
# normalized_retrieval_text
# ---------------------------------------------------------------------------


def test_normalized_retrieval_text_combines_title_and_snippet() -> None:
    result = normalized_retrieval_text("Gateway Token", "rotate before enabling")
    assert "gateway" in result
    assert "token" in result
    assert "rotate" in result


def test_normalized_retrieval_text_returns_string() -> None:
    assert isinstance(normalized_retrieval_text("A", "B"), str)


def test_normalized_retrieval_text_empty_inputs() -> None:
    result = normalized_retrieval_text("", "")
    assert result == ""


def test_normalized_retrieval_text_dedupes_separators() -> None:
    result = normalized_retrieval_text("  Title  ", "  Snippet  ")
    assert "  " not in result


# ---------------------------------------------------------------------------
# point_id
# ---------------------------------------------------------------------------


def test_point_id_has_uuid_like_format() -> None:
    pid = point_id(
        document_rel_path="docs/runbook.md",
        item_type="fact",
        start_line=1,
        end_line=5,
        snippet="The deploy process uses blue/green.",
    )
    parts = pid.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4
    assert len(parts[4]) == 12


def test_point_id_is_deterministic() -> None:
    pid1 = point_id(
        document_rel_path="MEMORY.md",
        item_type="fact",
        start_line=10,
        end_line=10,
        snippet="Alice owns the deployment playbook.",
    )
    pid2 = point_id(
        document_rel_path="MEMORY.md",
        item_type="fact",
        start_line=10,
        end_line=10,
        snippet="Alice owns the deployment playbook.",
    )
    assert pid1 == pid2


def test_point_id_differs_for_different_snippets() -> None:
    pid_alpha = point_id(
        document_rel_path="x.md", item_type="fact", start_line=1, end_line=1, snippet="alpha"
    )
    pid_beta = point_id(
        document_rel_path="x.md", item_type="fact", start_line=1, end_line=1, snippet="beta"
    )
    assert pid_alpha != pid_beta


def test_point_id_differs_for_different_paths() -> None:
    pid_a = point_id(
        document_rel_path="a.md", item_type="fact", start_line=1, end_line=1, snippet="same"
    )
    pid_b = point_id(
        document_rel_path="b.md", item_type="fact", start_line=1, end_line=1, snippet="same"
    )
    assert pid_a != pid_b


def test_point_id_differs_for_different_item_types() -> None:
    pid_fact = point_id(
        document_rel_path="x.md", item_type="fact", start_line=1, end_line=1, snippet="same"
    )
    pid_opinion = point_id(
        document_rel_path="x.md", item_type="opinion", start_line=1, end_line=1, snippet="same"
    )
    assert pid_fact != pid_opinion


def test_point_id_snippet_whitespace_normalized() -> None:
    pid1 = point_id(
        document_rel_path="x.md", item_type="fact", start_line=1, end_line=1, snippet="  text  "
    )
    pid2 = point_id(
        document_rel_path="x.md", item_type="fact", start_line=1, end_line=1, snippet="text"
    )
    assert pid1 == pid2


def test_point_id_all_hex_characters() -> None:
    pid = point_id(
        document_rel_path="x.md", item_type="section", start_line=0, end_line=0, snippet=""
    )
    raw = pid.replace("-", "")
    assert all(c in "0123456789abcdef" for c in raw)
