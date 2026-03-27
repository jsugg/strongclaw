"""Shared contracts for context providers."""

from __future__ import annotations

from typing import Final, Literal, cast

type ContextProvider = Literal["codebase"]
type ContextScale = Literal["small", "medium", "large"]

CONTEXT_PROVIDER_CODEBASE: Final[ContextProvider] = "codebase"
CONTEXT_PROVIDERS: Final[frozenset[str]] = frozenset({CONTEXT_PROVIDER_CODEBASE})
CONTEXT_SCALES: Final[tuple[ContextScale, ...]] = ("small", "medium", "large")


def require_context_provider(value: object, *, path: str) -> ContextProvider:
    """Validate and return one supported context provider."""
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    if value not in CONTEXT_PROVIDERS:
        allowed = ", ".join(sorted(CONTEXT_PROVIDERS))
        raise ValueError(f"{path} must be one of: {allowed}")
    return cast(ContextProvider, value)


def require_context_scale(value: object, *, path: str) -> ContextScale:
    """Validate and return one supported context scale."""
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    if value not in CONTEXT_SCALES:
        allowed = ", ".join(CONTEXT_SCALES)
        raise ValueError(f"{path} must be one of: {allowed}")
    if value == "small":
        return "small"
    if value == "medium":
        return "medium"
    return "large"
