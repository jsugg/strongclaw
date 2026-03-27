"""Generic namespace for execution-plane context providers."""

from clawops.context.contracts import (
    CONTEXT_PROVIDER_CODEBASE,
    CONTEXT_PROVIDERS,
    CONTEXT_SCALES,
    ContextProvider,
    ContextScale,
    require_context_provider,
    require_context_scale,
)

__all__ = [
    "CONTEXT_PROVIDER_CODEBASE",
    "CONTEXT_PROVIDERS",
    "CONTEXT_SCALES",
    "ContextProvider",
    "ContextScale",
    "require_context_provider",
    "require_context_scale",
]
