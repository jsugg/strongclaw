"""Unit coverage for recovery policy and planning helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from clawops.recovery.planner import build_backup_plan
from clawops.recovery.policy import (
    DEFAULT_RECOVERY_PROFILE,
    RECOVERY_PROFILES,
    ensure_recovery_profile,
    retention_for_profile,
)


def test_recovery_profile_defaults_to_control_plane() -> None:
    """Recovery should default to the control-plane profile."""
    assert DEFAULT_RECOVERY_PROFILE == "control-plane"


def test_recovery_profile_accepts_known_values() -> None:
    """Recovery profile validation should accept all declared profiles."""
    for profile in RECOVERY_PROFILES:
        assert ensure_recovery_profile(profile) == profile


def test_recovery_profile_rejects_unknown_value() -> None:
    """Recovery profile validation should fail for unsupported values."""
    with pytest.raises(ValueError, match="unsupported recovery profile"):
        ensure_recovery_profile("unknown-profile")


def test_backup_planner_tracks_include_exclude_roots(tmp_path: Path) -> None:
    """Backup planner should include OpenClaw state and exclude backup roots."""
    home_dir = tmp_path / "home"
    openclaw_state = home_dir / ".openclaw"
    backup_root = home_dir / ".local" / "state" / "strongclaw" / "backups"
    legacy_backup_root = openclaw_state / "backups"
    (openclaw_state / "logs").mkdir(parents=True, exist_ok=True)
    (openclaw_state / "logs" / "gateway.log").write_text("ready\n", encoding="utf-8")
    (legacy_backup_root / "old.tar.gz").parent.mkdir(parents=True, exist_ok=True)
    (legacy_backup_root / "old.tar.gz").write_text("old", encoding="utf-8")
    plan = build_backup_plan(
        profile="control-plane",
        include_root=openclaw_state,
        backup_root=backup_root,
        legacy_backup_root=legacy_backup_root,
    )

    assert plan.include_roots == (openclaw_state.resolve(),)
    assert plan.exclude_roots == (backup_root.resolve(), legacy_backup_root.resolve())
    assert plan.backend_candidates == ("openclaw-cli", "tar-fallback")
    assert plan.estimated_file_count == 1
    assert plan.retention == retention_for_profile("control-plane")
