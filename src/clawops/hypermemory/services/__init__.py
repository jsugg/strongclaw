"""Stateful service objects for the StrongClaw hypermemory engine.

These services are the first step towards composition over a single "god object"
engine. They are intentionally small, dependency-injected, and designed to keep
the module boundaries acyclic.
"""

from __future__ import annotations

from clawops.hypermemory.services.backend_service import BackendService
from clawops.hypermemory.services.canonical_store_service import CanonicalStoreService
from clawops.hypermemory.services.index_service import IndexService

__all__ = [
    "BackendService",
    "CanonicalStoreService",
    "IndexService",
]
