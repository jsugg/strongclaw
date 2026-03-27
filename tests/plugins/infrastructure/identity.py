"""Deterministic, worker-aware test identity generation."""

from __future__ import annotations

import os
import time
import uuid


def get_worker_id() -> str:
    """Return the xdist worker name, or ``main`` for serial runs."""
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


def make_test_id() -> str:
    """Return a collision-safe test identifier."""
    return f"{uuid.uuid4().hex[:8]}_{get_worker_id()}"


def make_resource_prefix() -> str:
    """Return a collision-safe resource prefix for external resource naming."""
    return f"{uuid.uuid4().hex[:8]}_{get_worker_id()}_{time.time_ns()}"
