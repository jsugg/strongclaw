"""Context provider registry."""

from __future__ import annotations

import pathlib

from clawops.context.codebase.service import CodebaseContextService, service_from_config
from clawops.context.contracts import ContextProvider, ContextScale


def create_context_service(
    *,
    provider: ContextProvider,
    config_path: pathlib.Path,
    repo: pathlib.Path,
    scale: ContextScale,
) -> CodebaseContextService:
    """Build the configured context provider service."""
    if provider == "codebase":
        return service_from_config(config_path, repo, scale=scale)
    raise AssertionError(f"unsupported context provider: {provider}")
