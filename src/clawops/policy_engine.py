"""Policy evaluation for external side effects and wrapper actions."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
from typing import Any, Mapping

from clawops.common import load_json, load_yaml, match_mapping

TERMINAL_ALLOW = "allow"
TERMINAL_DENY = "deny"
TERMINAL_REQUIRE_APPROVAL = "require_approval"


@dataclasses.dataclass(slots=True)
class Decision:
    """Structured policy result."""

    decision: str
    reasons: list[str]
    matched_rules: list[str]
    review_mode: str | None = None
    review_target: str | None = None
    review_reason: str | None = None
    review_policy_id: str | None = None
    delegate_to: str | None = None

    def review_payload(self) -> dict[str, str]:
        """Serialize the optional review metadata."""
        payload: dict[str, str] = {}
        if self.review_mode is not None:
            payload["review_mode"] = self.review_mode
        if self.review_target is not None:
            payload["review_target"] = self.review_target
        if self.review_reason is not None:
            payload["review_reason"] = self.review_reason
        if self.review_policy_id is not None:
            payload["review_policy_id"] = self.review_policy_id
        if self.delegate_to is not None:
            payload["delegate_to"] = self.delegate_to
        return payload

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision."""
        payload: dict[str, Any] = {
            "decision": self.decision,
            "reasons": self.reasons,
            "matched_rules": self.matched_rules,
        }
        payload.update(self.review_payload())
        return payload


class PolicyEngine:
    """Evaluate simple YAML policy bundles."""

    def __init__(self, policy: Mapping[str, Any]) -> None:
        self.policy = policy

    @classmethod
    def from_file(cls, path: pathlib.Path) -> "PolicyEngine":
        """Load policy data from YAML."""
        return cls(load_yaml(path))

    def evaluate(self, payload: Mapping[str, Any]) -> Decision:
        """Evaluate *payload* against the configured policy."""
        reasons: list[str] = []
        matched_rules: list[str] = []

        defaults = self.policy.get("defaults", {})
        default_decision = defaults.get("decision", TERMINAL_DENY)

        zone_name = str(payload.get("trust_zone", "unknown"))
        zone = self.policy.get("zones", {}).get(zone_name, {})
        action = str(payload.get("action", ""))
        category = str(payload.get("category", ""))

        if action in zone.get("deny_actions", []) or category in zone.get("deny_categories", []):
            reasons.append(f"zone:{zone_name}:action/category denied")
            return Decision(TERMINAL_DENY, reasons, matched_rules)

        allow_actions = zone.get("allow_actions", [])
        allow_categories = zone.get("allow_categories", [])
        if allow_actions or allow_categories:
            allowed = action in allow_actions or category in allow_categories
            if not allowed:
                reasons.append(f"zone:{zone_name}:not in allowlist")
                return Decision(TERMINAL_DENY, reasons, matched_rules)

        targets = self.policy.get("allowlists", {})
        target_kind = str(payload.get("target_kind", ""))
        target_value = str(payload.get("target", ""))
        if target_kind:
            allowed_targets = set(targets.get(target_kind, []))
            if allowed_targets and target_value not in allowed_targets:
                reasons.append(f"target:{target_kind}:not allowlisted")
                return Decision(TERMINAL_DENY, reasons, matched_rules)

        for rule in self.policy.get("rules", []):
            when = rule.get("when", {})
            if not isinstance(when, Mapping):
                continue
            if not match_mapping(when, payload):
                continue
            rule_id = str(rule.get("id", f"rule-{len(matched_rules)+1}"))
            matched_rules.append(rule_id)
            outcome = str(rule.get("decision", default_decision))
            note = str(rule.get("reason", rule_id))
            reasons.append(note)
            if outcome == TERMINAL_DENY:
                return Decision(outcome, reasons, matched_rules)
            if outcome == TERMINAL_REQUIRE_APPROVAL:
                return self._build_review_decision(
                    payload=payload,
                    decision=outcome,
                    reasons=reasons,
                    matched_rules=matched_rules,
                    review_policy_id=rule_id,
                    fallback_reason=note,
                )

        approval_rules = self.policy.get("approval", {})
        review_policy_id: str | None = None
        if action in approval_rules.get("require_for_actions", []):
            review_policy_id = f"approval.actions.{action}"
        elif category in approval_rules.get("require_for_categories", []):
            review_policy_id = f"approval.categories.{category}"
        if review_policy_id is not None:
            reasons.append("approval required by approval matrix")
            return self._build_review_decision(
                payload=payload,
                decision=TERMINAL_REQUIRE_APPROVAL,
                reasons=reasons,
                matched_rules=matched_rules,
                review_policy_id=review_policy_id,
                fallback_reason=reasons[-1],
            )

        return self._build_review_decision(
            payload=payload,
            decision=default_decision,
            reasons=reasons or ["default"],
            matched_rules=matched_rules,
            review_policy_id="defaults.decision",
            fallback_reason=reasons[-1] if reasons else "default",
        )

    def _build_review_decision(
        self,
        *,
        payload: Mapping[str, Any],
        decision: str,
        reasons: list[str],
        matched_rules: list[str],
        review_policy_id: str,
        fallback_reason: str,
    ) -> Decision:
        """Build a decision and attach additive review metadata when needed."""
        if decision != TERMINAL_REQUIRE_APPROVAL:
            return Decision(decision, reasons, matched_rules)

        review_config = self._resolve_review_config(payload)
        mode_value = review_config.get("mode", "manual")
        review_mode = str(mode_value).strip() or "manual"
        delegate_to_value = review_config.get("delegate_to")
        delegate_to = (
            str(delegate_to_value).strip()
            if isinstance(delegate_to_value, str) and delegate_to_value.strip()
            else None
        )
        review_target_value = review_config.get("review_target", review_config.get("target"))
        review_target = (
            str(review_target_value).strip()
            if isinstance(review_target_value, str) and review_target_value.strip()
            else delegate_to
        )
        reason_value = review_config.get("reason")
        review_reason = (
            str(reason_value).strip()
            if isinstance(reason_value, str) and reason_value.strip()
            else fallback_reason
        )
        return Decision(
            decision=decision,
            reasons=reasons,
            matched_rules=matched_rules,
            review_mode=review_mode,
            review_target=review_target,
            review_reason=review_reason,
            review_policy_id=review_policy_id,
            delegate_to=delegate_to,
        )

    def _resolve_review_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve optional review overrides for the payload."""
        review_block = self.policy.get("review", {})
        if not isinstance(review_block, Mapping):
            return {}

        merged: dict[str, Any] = {}
        defaults = review_block.get("defaults", {})
        if isinstance(defaults, Mapping):
            merged.update(defaults)

        categories = review_block.get("categories", {})
        category = str(payload.get("category", ""))
        if isinstance(categories, Mapping):
            category_config = categories.get(category, {})
            if isinstance(category_config, Mapping):
                merged.update(category_config)

        actions = review_block.get("actions", {})
        action = str(payload.get("action", ""))
        if isinstance(actions, Mapping):
            action_config = actions.get(action, {})
            if isinstance(action_config, Mapping):
                merged.update(action_config)

        return merged

    @staticmethod
    def load_payload(path: pathlib.Path) -> Mapping[str, Any]:
        """Load an input payload from JSON."""
        return load_json(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=pathlib.Path)
    parser.add_argument("--input", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    engine = PolicyEngine.from_file(args.policy)
    decision = engine.evaluate(engine.load_payload(args.input))
    print(json.dumps(decision.to_dict(), indent=2, sort_keys=True))
    return 0
