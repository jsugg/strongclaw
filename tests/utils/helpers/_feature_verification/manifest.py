"""Typed loader and validator for the checked-in feature matrix (``tests/feature_matrix.yaml``)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import yaml

type PlatformName = Literal["linux", "macos"]
type Verification = Literal["live", "fake-model", "stub"]
type FeatureStatus = Literal["verifiable", "unverifiable"]

MANIFEST_SCHEMA_VERSION: Final[int] = 1
PLATFORMS: Final[frozenset[str]] = frozenset({"linux", "macos"})
VERIFICATIONS: Final[frozenset[str]] = frozenset({"live", "fake-model", "stub"})
STATUSES: Final[frozenset[str]] = frozenset({"verifiable", "unverifiable"})
FEATURE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
CALL_SITE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?P<path>[^:\s]+):(?P<line>[1-9]\d*)$")
TEST_NODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>tests/suites/e2e/feature_verification/test_[a-z0-9_]+\.py)::(?P<name>test_[a-z0-9_]+)$"
)


class FeatureVerificationError(RuntimeError):
    """Raised when the feature matrix or a lane result is invalid."""


@dataclass(frozen=True, slots=True)
class Lane:
    """One provisioned CI host the suite runs on."""

    lane_id: str
    platform: PlatformName
    profile: str
    scenario: str
    activate_services: bool
    description: str


@dataclass(frozen=True, slots=True)
class Feature:
    """One product feature and the test that verifies it."""

    feature_id: str
    category: str
    title: str
    call_sites: tuple[str, ...]
    status: FeatureStatus
    verification: Verification | None
    lanes: tuple[str, ...]
    test: str | None
    reason: str | None
    limitations: str | None

    @property
    def test_path(self) -> str | None:
        """Return the repo-relative test module path."""
        return None if self.test is None else self.test.split("::", 1)[0]

    @property
    def test_name(self) -> str | None:
        """Return the test function name."""
        return None if self.test is None else self.test.split("::", 1)[1]


@dataclass(frozen=True, slots=True)
class FeatureManifest:
    """Validated feature matrix."""

    lanes: Mapping[str, Lane]
    features: tuple[Feature, ...]

    def feature(self, feature_id: str) -> Feature:
        """Return one feature by id."""
        for feature in self.features:
            if feature.feature_id == feature_id:
                return feature
        raise FeatureVerificationError(f"unknown feature {feature_id!r}")

    def features_for_lane(self, lane_id: str) -> tuple[Feature, ...]:
        """Return the verifiable features scheduled on one lane."""
        return tuple(feature for feature in self.features if lane_id in feature.lanes)

    def feature_by_test(self) -> dict[str, Feature]:
        """Map each test node id to the feature it verifies."""
        return {feature.test: feature for feature in self.features if feature.test is not None}

    def platforms(self, feature: Feature) -> tuple[PlatformName, ...]:
        """Return the platforms one feature runs on, derived from its lanes."""
        return tuple(sorted({self.lanes[lane_id].platform for lane_id in feature.lanes}))


def _mapping(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FeatureVerificationError(f"{path} must be a mapping")
    return cast(Mapping[str, object], value)


def _string(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FeatureVerificationError(f"{path} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, *, path: str) -> str | None:
    return None if value is None else _string(value, path=path)


def _string_tuple(value: object, *, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise FeatureVerificationError(f"{path} must be a list of strings")
    return tuple(
        _string(item, path=f"{path}[{index}]")
        for index, item in enumerate(cast(Sequence[object], value))
    )


def _choice(value: object, *, allowed: frozenset[str], path: str) -> str:
    text = _string(value, path=path)
    if text not in allowed:
        raise FeatureVerificationError(f"{path} must be one of {sorted(allowed)}, got {text!r}")
    return text


def _load_lane(lane_id: str, raw: object) -> Lane:
    path = f"lanes.{lane_id}"
    payload = _mapping(raw, path=path)
    activate = payload.get("activate_services")
    if not isinstance(activate, bool):
        raise FeatureVerificationError(f"{path}.activate_services must be a boolean")
    return Lane(
        lane_id=lane_id,
        platform=cast(
            PlatformName,
            _choice(payload.get("platform"), allowed=PLATFORMS, path=f"{path}.platform"),
        ),
        profile=_string(payload.get("profile"), path=f"{path}.profile"),
        scenario=_string(payload.get("scenario"), path=f"{path}.scenario"),
        activate_services=activate,
        description=_string(payload.get("description"), path=f"{path}.description"),
    )


def _load_feature(index: int, raw: object, *, lanes: Mapping[str, Lane]) -> Feature:
    path = f"features[{index}]"
    payload = _mapping(raw, path=path)
    feature_id = _string(payload.get("id"), path=f"{path}.id")
    if FEATURE_ID_PATTERN.fullmatch(feature_id) is None:
        raise FeatureVerificationError(f"{path}.id {feature_id!r} must be dotted kebab-case")
    path = f"feature {feature_id}"
    status = cast(
        FeatureStatus,
        _choice(payload.get("status", "verifiable"), allowed=STATUSES, path=f"{path}.status"),
    )
    call_sites = _string_tuple(payload.get("call_sites"), path=f"{path}.call_sites")
    for call_site in call_sites:
        if CALL_SITE_PATTERN.fullmatch(call_site) is None:
            raise FeatureVerificationError(
                f"{path}.call_sites entry {call_site!r} is not file:line"
            )
    feature_lanes = _string_tuple(payload.get("lanes"), path=f"{path}.lanes")
    unknown_lanes = sorted(set(feature_lanes) - set(lanes))
    if unknown_lanes:
        raise FeatureVerificationError(f"{path} references unknown lanes {unknown_lanes}")
    test = _optional_string(payload.get("test"), path=f"{path}.test")
    if test is not None and TEST_NODE_PATTERN.fullmatch(test) is None:
        raise FeatureVerificationError(f"{path}.test {test!r} is not a feature-suite node id")
    raw_verification = payload.get("verification")
    verification = (
        None
        if raw_verification is None
        else cast(
            Verification,
            _choice(raw_verification, allowed=VERIFICATIONS, path=f"{path}.verification"),
        )
    )
    reason = _optional_string(payload.get("reason"), path=f"{path}.reason")
    if status == "verifiable":
        if test is None or not feature_lanes or verification is None:
            raise FeatureVerificationError(
                f"{path} is verifiable, so it needs a test, at least one lane, and a verification"
            )
    elif reason is None or test is not None or feature_lanes:
        raise FeatureVerificationError(
            f"{path} is unverifiable, so it needs a reason and must not schedule a test"
        )
    if not call_sites:
        raise FeatureVerificationError(f"{path} must cite at least one call site")
    return Feature(
        feature_id=feature_id,
        category=_string(payload.get("category"), path=f"{path}.category"),
        title=_string(payload.get("title"), path=f"{path}.title"),
        call_sites=call_sites,
        status=status,
        verification=verification,
        lanes=feature_lanes,
        test=test,
        reason=reason,
        limitations=_optional_string(payload.get("limitations"), path=f"{path}.limitations"),
    )


def load_manifest(path: Path) -> FeatureManifest:
    """Load and validate the feature matrix."""
    if not path.is_file():
        raise FeatureVerificationError(f"feature matrix not found at {path}")
    document = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), path="manifest")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise FeatureVerificationError(f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    raw_lanes = _mapping(document.get("lanes"), path="lanes")
    lanes = {str(lane_id): _load_lane(str(lane_id), raw) for lane_id, raw in raw_lanes.items()}
    raw_features = document.get("features")
    if not isinstance(raw_features, list) or not raw_features:
        raise FeatureVerificationError("features must be a non-empty list")
    features = tuple(
        _load_feature(index, raw, lanes=lanes)
        for index, raw in enumerate(cast(list[object], raw_features))
    )
    seen_ids: set[str] = set()
    seen_tests: set[str] = set()
    for feature in features:
        if feature.feature_id in seen_ids:
            raise FeatureVerificationError(f"duplicate feature id {feature.feature_id!r}")
        seen_ids.add(feature.feature_id)
        if feature.test is not None:
            if feature.test in seen_tests:
                raise FeatureVerificationError(f"test {feature.test!r} maps to several features")
            seen_tests.add(feature.test)
    return FeatureManifest(lanes=lanes, features=features)
