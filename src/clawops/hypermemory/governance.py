"""Scope validation and governance helpers for StrongClaw hypermemory."""

from __future__ import annotations

from clawops.hypermemory.models import GovernanceConfig


def matches_scope_pattern(scope: str, pattern: str) -> bool:
    """Return whether *scope* matches a configured scope pattern."""
    if pattern.endswith(":"):
        return scope.startswith(pattern)
    return scope == pattern


def is_scope_allowed(scope: str, patterns: tuple[str, ...]) -> bool:
    """Return whether *scope* is allowed by any configured pattern."""
    return any(matches_scope_pattern(scope, pattern) for pattern in patterns)


def validate_scope(scope: str) -> str:
    """Validate and normalize a memory scope string."""
    value = scope.strip()
    if not value:
        raise ValueError("scope must not be empty")
    if ":" in value:
        prefix, suffix = value.split(":", 1)
        if not prefix or not suffix:
            raise ValueError(f"invalid scope: {scope}")
        return f"{prefix.strip()}:{suffix.strip()}"
    if value != "global":
        raise ValueError(f"invalid scope: {scope}")
    return value


def ensure_writable_scope(scope: str, governance: GovernanceConfig) -> str:
    """Validate a writable scope and enforce the configured allowlist."""
    normalized = validate_scope(scope)
    if not is_scope_allowed(normalized, governance.writable_scope_patterns):
        raise PermissionError(f"writes are not allowed for scope {normalized}")
    return normalized


def is_scope_visible(scope: str, governance: GovernanceConfig) -> bool:
    """Return whether *scope* is visible under the configured read rules."""
    return is_scope_allowed(scope, governance.readable_scope_patterns)


def should_auto_apply(scope: str, governance: GovernanceConfig) -> bool:
    """Return whether a reflected proposal may be applied immediately."""
    return is_scope_allowed(scope, governance.auto_apply_scope_patterns)
