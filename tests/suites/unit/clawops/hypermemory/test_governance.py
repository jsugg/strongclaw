"""Unit tests for hypermemory/governance.py."""

from __future__ import annotations

import pytest

from clawops.hypermemory.governance import (
    ensure_writable_scope,
    is_scope_allowed,
    is_scope_visible,
    matches_scope_pattern,
    should_auto_apply,
    validate_scope,
)
from clawops.hypermemory.models import GovernanceConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _governance(
    *,
    readable: tuple[str, ...] = ("global",),
    writable: tuple[str, ...] = ("global",),
    auto_apply: tuple[str, ...] = ("global",),
) -> GovernanceConfig:
    return GovernanceConfig(
        default_scope="global",
        readable_scope_patterns=readable,
        writable_scope_patterns=writable,
        auto_apply_scope_patterns=auto_apply,
    )


# ---------------------------------------------------------------------------
# matches_scope_pattern
# ---------------------------------------------------------------------------


def test_matches_scope_pattern_exact_match() -> None:
    assert matches_scope_pattern("global", "global")


def test_matches_scope_pattern_exact_mismatch() -> None:
    assert not matches_scope_pattern("project:foo", "global")


def test_matches_scope_pattern_prefix_match() -> None:
    assert matches_scope_pattern("project:foo", "project:")


def test_matches_scope_pattern_prefix_no_match() -> None:
    assert not matches_scope_pattern("global", "project:")


def test_matches_scope_pattern_prefix_requires_trailing_colon() -> None:
    # "project" without trailing colon is treated as an exact-match pattern
    assert not matches_scope_pattern("project:foo", "project")


def test_matches_scope_pattern_empty_scope_vs_global() -> None:
    assert not matches_scope_pattern("", "global")


def test_matches_scope_pattern_nested_prefix() -> None:
    assert matches_scope_pattern("team:alpha:sub", "team:")


# ---------------------------------------------------------------------------
# is_scope_allowed
# ---------------------------------------------------------------------------


def test_is_scope_allowed_single_matching_pattern() -> None:
    assert is_scope_allowed("global", ("global",))


def test_is_scope_allowed_multiple_patterns_first_matches() -> None:
    assert is_scope_allowed("global", ("global", "project:"))


def test_is_scope_allowed_multiple_patterns_second_matches() -> None:
    assert is_scope_allowed("project:foo", ("global", "project:"))


def test_is_scope_allowed_no_match() -> None:
    assert not is_scope_allowed("team:bar", ("global", "project:"))


def test_is_scope_allowed_empty_patterns() -> None:
    assert not is_scope_allowed("global", ())


# ---------------------------------------------------------------------------
# validate_scope
# ---------------------------------------------------------------------------


def test_validate_scope_global() -> None:
    assert validate_scope("global") == "global"


def test_validate_scope_namespaced() -> None:
    assert validate_scope("project:strongclaw") == "project:strongclaw"


def test_validate_scope_strips_whitespace() -> None:
    assert validate_scope("  global  ") == "global"


def test_validate_scope_strips_namespace_whitespace() -> None:
    result = validate_scope("  project : foo  ")
    assert result == "project:foo"


def test_validate_scope_empty_raises() -> None:
    with pytest.raises(ValueError, match="scope must not be empty"):
        validate_scope("")


def test_validate_scope_whitespace_only_raises() -> None:
    with pytest.raises(ValueError, match="scope must not be empty"):
        validate_scope("   ")


def test_validate_scope_invalid_non_global_no_colon() -> None:
    with pytest.raises(ValueError, match="invalid scope"):
        validate_scope("notglobal")


def test_validate_scope_missing_prefix_raises() -> None:
    with pytest.raises(ValueError, match="invalid scope"):
        validate_scope(":suffix")


def test_validate_scope_missing_suffix_raises() -> None:
    with pytest.raises(ValueError, match="invalid scope"):
        validate_scope("prefix:")


# ---------------------------------------------------------------------------
# ensure_writable_scope
# ---------------------------------------------------------------------------


def test_ensure_writable_scope_allowed() -> None:
    gov = _governance(writable=("global",))
    assert ensure_writable_scope("global", gov) == "global"


def test_ensure_writable_scope_prefix_allowed() -> None:
    gov = _governance(writable=("project:",))
    assert ensure_writable_scope("project:foo", gov) == "project:foo"


def test_ensure_writable_scope_denied_raises() -> None:
    gov = _governance(writable=("global",))
    with pytest.raises(PermissionError, match="writes are not allowed"):
        ensure_writable_scope("project:foo", gov)


def test_ensure_writable_scope_invalid_scope_raises() -> None:
    gov = _governance(writable=("global",))
    with pytest.raises(ValueError):
        ensure_writable_scope("notvalid", gov)


# ---------------------------------------------------------------------------
# is_scope_visible
# ---------------------------------------------------------------------------


def test_is_scope_visible_readable() -> None:
    gov = _governance(readable=("global", "project:"))
    assert is_scope_visible("global", gov)


def test_is_scope_visible_prefix_readable() -> None:
    gov = _governance(readable=("project:",))
    assert is_scope_visible("project:foo", gov)


def test_is_scope_visible_not_readable() -> None:
    gov = _governance(readable=("global",))
    assert not is_scope_visible("project:secret", gov)


def test_is_scope_visible_empty_readable_patterns() -> None:
    gov = _governance(readable=())
    assert not is_scope_visible("global", gov)


# ---------------------------------------------------------------------------
# should_auto_apply
# ---------------------------------------------------------------------------


def test_should_auto_apply_allowed() -> None:
    gov = _governance(auto_apply=("global",))
    assert should_auto_apply("global", gov)


def test_should_auto_apply_not_allowed() -> None:
    gov = _governance(auto_apply=())
    assert not should_auto_apply("global", gov)


def test_should_auto_apply_prefix_allowed() -> None:
    gov = _governance(auto_apply=("project:",))
    assert should_auto_apply("project:foo", gov)


def test_should_auto_apply_exact_not_matched_by_prefix() -> None:
    gov = _governance(auto_apply=("project:",))
    assert not should_auto_apply("global", gov)
