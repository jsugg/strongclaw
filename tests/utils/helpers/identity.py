"""Compatibility re-export for infrastructure identity helpers."""

from __future__ import annotations

from tests.plugins.infrastructure.identity import get_worker_id, make_resource_prefix, make_test_id

__all__ = ["get_worker_id", "make_resource_prefix", "make_test_id"]
