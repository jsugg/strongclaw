"""Memory lifecycle scoring and tier management for hypermemory."""

from __future__ import annotations

import dataclasses
import math

from clawops.hypermemory.models import DecayConfig, Tier


def compute_decay_score(
    *,
    age_days: float,
    access_count: int,
    importance: float,
    tier: Tier,
    config: DecayConfig,
) -> float:
    """Return a bounded lifecycle score in the closed interval [0.0, 1.0]."""
    beta = {
        "core": config.beta_core,
        "working": config.beta_working,
        "peripheral": config.beta_peripheral,
    }.get(tier, config.beta_working)
    effective_half_life = config.half_life_days * math.exp(0.5 * importance)
    effective_half_life *= 1.0 + (0.3 * math.sqrt(max(access_count, 0)))
    lam = math.log(2.0) / max(effective_half_life, 1.0)
    recency = math.exp(-lam * (max(age_days, 0.0) ** beta))
    frequency = 1.0 - math.exp(-max(access_count, 0) / 5.0)
    composite = (
        (config.recency_weight * recency)
        + (config.frequency_weight * frequency)
        + (config.intrinsic_weight * importance)
    )
    return max(0.0, min(1.0, composite))


@dataclasses.dataclass(frozen=True, slots=True)
class TierManager:
    """Evaluate tier transitions for memory lifecycle management."""

    config: DecayConfig

    def evaluate_tier(
        self,
        *,
        current_tier: Tier,
        composite: float,
        access_count: int,
        importance: float,
        age_days: float,
    ) -> Tier:
        """Return the stable tier for the provided metrics."""
        if current_tier != "core" and self.should_promote_to_core(
            composite=composite,
            access_count=access_count,
            importance=importance,
        ):
            return "core"
        if current_tier == "core" and self.should_demote_from_core(
            composite=composite,
            access_count=access_count,
        ):
            return "working"
        if current_tier == "peripheral" and self.should_promote_to_working(
            composite=composite,
            access_count=access_count,
        ):
            return "working"
        if current_tier != "peripheral" and self.should_demote_to_peripheral(
            composite=composite,
            access_count=access_count,
            age_days=age_days,
        ):
            return "peripheral"
        return current_tier

    def should_promote_to_core(
        self,
        *,
        composite: float,
        access_count: int,
        importance: float,
    ) -> bool:
        """Return whether the item should graduate into the core tier."""
        return (
            access_count >= self.config.promote_to_core_access
            and composite >= self.config.promote_to_core_composite
            and importance >= self.config.promote_to_core_importance
        )

    def should_promote_to_working(self, *, composite: float, access_count: int) -> bool:
        """Return whether a peripheral item should return to working memory."""
        return (
            access_count >= self.config.promote_to_working_access
            or composite >= self.config.promote_to_working_composite
        )

    def should_demote_to_peripheral(
        self,
        *,
        composite: float,
        access_count: int,
        age_days: float,
    ) -> bool:
        """Return whether a non-core item should demote to peripheral."""
        return (
            composite <= self.config.demote_to_peripheral_composite
            and age_days >= self.config.demote_to_peripheral_age_days
            and access_count <= self.config.demote_to_peripheral_access
        )

    def should_demote_from_core(self, *, composite: float, access_count: int) -> bool:
        """Return whether a core item has decayed enough to lose core status."""
        return (
            composite <= self.config.demote_from_core_composite
            and access_count <= self.config.demote_from_core_access
        )
