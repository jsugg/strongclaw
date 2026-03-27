"""Codebase-specific context provider exports."""

from clawops.context.codebase.service import (
    CodebaseContextConfig,
    CodebaseContextService,
    IndexedFile,
    IndexStats,
    SearchHit,
    load_config,
    main,
    service_from_config,
)

__all__ = [
    "CodebaseContextConfig",
    "CodebaseContextService",
    "IndexStats",
    "IndexedFile",
    "SearchHit",
    "load_config",
    "main",
    "service_from_config",
]
