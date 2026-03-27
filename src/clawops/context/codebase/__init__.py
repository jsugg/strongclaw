"""Codebase context provider."""

from __future__ import annotations

from clawops.context.codebase.service import (
    CodebaseContextConfig,
    CodebaseContextService,
    IndexedFile,
    IndexStats,
    SearchHit,
    load_config,
    service_from_config,
)

__all__ = [
    "CodebaseContextConfig",
    "CodebaseContextService",
    "IndexedFile",
    "IndexStats",
    "SearchHit",
    "load_config",
    "service_from_config",
]
