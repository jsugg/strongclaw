"""Compatibility re-export for the infrastructure ``TestContext`` runtime."""

from __future__ import annotations

from tests.plugins.infrastructure.context import CONTEXT_KEY, ResourceRecord, TestContext

__all__ = ["CONTEXT_KEY", "ResourceRecord", "TestContext"]
