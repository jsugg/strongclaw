"""Named environment profiles for the test infrastructure runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from tests.plugins.infrastructure.types import ProfileOverrideValue, ProfileValue, TestProfileName

type ProfileOverrides = dict[str, ProfileOverrideValue]
type ResolvedProfileEnv = dict[str, str | None]


def _empty_resolved_profile_env() -> ResolvedProfileEnv:
    return {}


def _normalize_value(value: ProfileValue) -> str:
    return value if isinstance(value, str) else str(value)


def _normalize_overrides(
    overrides: dict[str, ProfileOverrideValue] | None,
) -> ResolvedProfileEnv:
    if overrides is None:
        return {}
    normalized: ResolvedProfileEnv = {}
    for key, value in overrides.items():
        normalized[key] = None if value is None else _normalize_value(value)
    return normalized


@dataclass(frozen=True, slots=True)
class TestProfile:
    """Declarative environment profile applied by the infrastructure runtime."""

    name: TestProfileName
    description: str
    env: ResolvedProfileEnv = field(default_factory=_empty_resolved_profile_env)
    required_overrides: frozenset[str] = frozenset()

    def resolve_env(
        self,
        *,
        overrides: dict[str, ProfileOverrideValue] | None = None,
    ) -> ResolvedProfileEnv:
        """Return the final environment mutations for this profile."""
        resolved: ResolvedProfileEnv = dict(self.env)
        normalized_overrides = _normalize_overrides(overrides)
        missing = self.required_overrides.difference(normalized_overrides)
        if missing:
            missing_names = ", ".join(sorted(missing))
            raise ValueError(f"profile '{self.name}' requires overrides for: {missing_names}")
        resolved.update(normalized_overrides)
        return resolved


_PROFILES: dict[TestProfileName, TestProfile] = {
    "structured_logs": TestProfile(
        name="structured_logs",
        description="Enable structured logging for the current test.",
        env={"CLAWOPS_STRUCTURED_LOGS": "1"},
    ),
    "fresh_host_push": TestProfile(
        name="fresh_host_push",
        description="Model a GitHub Actions push event for fresh-host tests.",
        env={"GITHUB_EVENT_NAME": "push"},
    ),
    "fresh_host_macos_colima": TestProfile(
        name="fresh_host_macos_colima",
        description="Model the hosted macOS fresh-host runtime using Colima.",
        env={
            "GITHUB_EVENT_NAME": "push",
            "DEFAULT_MACOS_RUNTIME_PROVIDER": "colima",
        },
    ),
    "retry_safe": TestProfile(
        name="retry_safe",
        description="Enable the safe HTTP retry mode for wrapper tests.",
        env={"CLAWOPS_HTTP_RETRY_MODE": "safe"},
    ),
    "retry_off": TestProfile(
        name="retry_off",
        description="Disable HTTP retries for wrapper tests.",
        env={"CLAWOPS_HTTP_RETRY_MODE": "off"},
    ),
    "model_setup_skip": TestProfile(
        name="model_setup_skip",
        description="Skip model bootstrap side effects during test setup.",
        env={"OPENCLAW_MODEL_SETUP_MODE": "skip"},
    ),
    "workflow_state": TestProfile(
        name="workflow_state",
        description="Declare the workflow state directory for the current test.",
        required_overrides=frozenset({"STRONGCLAW_STATE_DIR"}),
    ),
}


def available_profiles() -> tuple[TestProfileName, ...]:
    """Return the stable list of available profile names."""
    return tuple(sorted(_PROFILES))


def resolve_profile(name: str) -> TestProfile:
    """Return the named profile, or raise a helpful error for unknown names."""
    try:
        return _PROFILES[cast(TestProfileName, name)]
    except KeyError as exc:  # pragma: no cover - defensive guard.
        available = ", ".join(available_profiles())
        raise KeyError(f"unknown test profile '{name}'. Available profiles: {available}") from exc
