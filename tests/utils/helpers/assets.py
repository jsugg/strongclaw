"""Reusable helpers for StrongClaw runtime asset tests."""

from __future__ import annotations

from pathlib import Path


def make_asset_root(root: Path) -> Path:
    """Create and return one minimal valid StrongClaw asset root."""
    (root / "platform").mkdir(parents=True, exist_ok=True)
    return root
