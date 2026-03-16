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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision."""
        return {
            "decision": self.decision,
            "reasons": self.reasons,
            "matched_rules": self.matched_rules,
        }


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
                return Decision(outcome, reasons, matched_rules)

        approval_rules = self.policy.get("approval", {})
        if action in approval_rules.get(
            "require_for_actions", []
        ) or category in approval_rules.get("require_for_categories", []):
            reasons.append("approval required by approval matrix")
            return Decision(TERMINAL_REQUIRE_APPROVAL, reasons, matched_rules)

        return Decision(default_decision, reasons or ["default"], matched_rules)

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
