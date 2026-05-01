"""Retention helpers for recovery profiles."""

from __future__ import annotations

from clawops.recovery.models import RecoveryProfile
from clawops.recovery.policy import retention_for_profile


def retention_policy_payload(profile: RecoveryProfile) -> dict[str, object]:
    """Return the retention payload for operator-facing plan output."""
    return retention_for_profile(profile)
