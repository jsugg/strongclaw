"""Contract tests for the shipped codebase provider config."""

from __future__ import annotations

from clawops.context.codebase.service import load_config
from tests.utils.helpers.repo import REPO_ROOT


def test_shipped_codebase_provider_enables_hybrid_lane() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")

    assert config.embedding.enabled is True
    assert config.qdrant.enabled is True
    assert config.qdrant.collection == "strongclaw-codebase-context"
    assert config.hybrid.fusion == "rrf"
