"""Recovery policy defaults and profile validation."""

from __future__ import annotations

from typing import TypeGuard

from clawops.recovery.models import RecoveryProfile

RECOVERY_PROFILES: tuple[RecoveryProfile, ...] = (
    "control-plane",
    "devflow-checkpoint",
    "hypermemory-fast",
    "full-data-plane",
)
DEFAULT_RECOVERY_PROFILE: RecoveryProfile = "control-plane"


def ensure_recovery_profile(raw_profile: str) -> RecoveryProfile:
    """Validate and normalize one recovery profile."""
    if not _is_recovery_profile(raw_profile):
        choices = ", ".join(RECOVERY_PROFILES)
        raise ValueError(
            f"unsupported recovery profile {raw_profile!r}; expected one of: {choices}"
        )
    return raw_profile


def _is_recovery_profile(raw_profile: str) -> TypeGuard[RecoveryProfile]:
    """Return whether *raw_profile* is one of the declared recovery profiles."""
    return raw_profile in RECOVERY_PROFILES


def retention_for_profile(profile: RecoveryProfile) -> dict[str, object]:
    """Return profile-specific retention policy metadata."""
    if profile == "control-plane":
        return {"daily": 7, "weekly": 2}
    if profile == "devflow-checkpoint":
        return {"active_run_latest": 3, "completed_runs": 5, "completed_retention_days": 7}
    if profile == "hypermemory-fast":
        return {"checkpoints": 5}
    return {"weekly": 2}
