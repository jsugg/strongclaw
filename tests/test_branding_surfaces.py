"""Regression tests for remaining shipped branding surfaces."""

from __future__ import annotations

import pathlib


def test_shipped_surfaces_do_not_reference_obsolete_repo_identity() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    files = (
        repo_root / "scripts/bootstrap/bootstrap_fork.sh",
        repo_root / "scripts/bootstrap/verify_baseline.sh",
        repo_root / "platform/workers/acpx/README.md",
    )

    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "openclaw-platform-bootstrap" not in text
        assert "OpenClaw Platform Bootstrap" not in text


def test_bootstrap_fork_defaults_to_repo_local_upstream_checkout() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bootstrap_fork = (repo_root / "scripts/bootstrap/bootstrap_fork.sh").read_text(encoding="utf-8")

    assert 'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"' in bootstrap_fork
    assert 'DEST="${1:-$ROOT/repo/upstream}"' in bootstrap_fork


def test_verify_baseline_uses_current_memory_search_query() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    verify_baseline = (repo_root / "scripts/bootstrap/verify_baseline.sh").read_text(
        encoding="utf-8"
    )

    assert 'openclaw memory search --query "ClawOps" --max-results 1' in verify_baseline
