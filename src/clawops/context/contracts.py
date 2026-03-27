"""Shared contracts for context providers."""

from __future__ import annotations

from typing import Literal

type ContextProvider = Literal["codebase"]
type ContextScale = Literal["small", "medium", "large"]

CONTEXT_PROVIDERS: frozenset[ContextProvider] = frozenset({"codebase"})
CONTEXT_SCALES: frozenset[ContextScale] = frozenset({"small", "medium", "large"})


def validate_context_provider(value: object, *, path: str) -> ContextProvider:
    """Validate and normalize a context provider name."""
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    if value == "codebase":
        return "codebase"
    allowed = ", ".join(sorted(CONTEXT_PROVIDERS))
    raise ValueError(f"{path} must be one of: {allowed}")


def validate_context_scale(value: object, *, path: str) -> ContextScale:
    """Validate and normalize a context scale."""
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    if value == "small":
        return "small"
    if value == "medium":
        return "medium"
    if value == "large":
        return "large"
    allowed = ", ".join(sorted(CONTEXT_SCALES))
    raise ValueError(f"{path} must be one of: {allowed}")
