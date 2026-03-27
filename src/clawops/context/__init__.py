"""Generic context provider namespace for clawops."""

from __future__ import annotations

from clawops.context.cli import main
from clawops.context.contracts import (
    CONTEXT_PROVIDERS,
    CONTEXT_SCALES,
    ContextProvider,
    ContextScale,
)

__all__ = [
    "CONTEXT_PROVIDERS",
    "CONTEXT_SCALES",
    "ContextProvider",
    "ContextScale",
    "main",
]
