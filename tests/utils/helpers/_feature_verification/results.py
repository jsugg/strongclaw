"""Per-lane feature results written by the pytest plugin and read by the reporter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, Literal, cast

from tests.utils.helpers._feature_verification.manifest import FeatureVerificationError

type TestOutcome = Literal["passed", "failed", "error", "skipped", "not-run"]
type FeatureVerdict = Literal["PASS", "FAIL", "UNVERIFIABLE"]

RESULT_SCHEMA_VERSION: Final[int] = 1
TEST_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"passed", "failed", "error", "skipped", "not-run"}
)
# A test that discovers at runtime that its feature cannot be exercised skips with this prefix.
UNVERIFIABLE_SKIP_PREFIX: Final[str] = "unverifiable:"
MAX_MESSAGE_CHARS: Final[int] = 4000


@dataclass(slots=True)
class FeatureResult:
    """Outcome of one feature test on one lane."""

    feature: str
    test: str
    outcome: TestOutcome
    duration_seconds: float
    message: str = ""

    @property
    def verdict(self) -> FeatureVerdict:
        """Map the pytest outcome onto the report vocabulary."""
        if self.outcome == "passed":
            return "PASS"
        if self.outcome == "skipped" and self.message.startswith(UNVERIFIABLE_SKIP_PREFIX):
            return "UNVERIFIABLE"
        return "FAIL"


def _empty_results() -> list[FeatureResult]:
    return []


def _empty_versions() -> dict[str, str]:
    return {}


@dataclass(slots=True)
class LaneResults:
    """All feature outcomes recorded on one lane."""

    lane: str
    platform: str
    profile: str
    started_at: str
    finished_at: str = ""
    versions: dict[str, str] = field(default_factory=_empty_versions)
    results: list[FeatureResult] = field(default_factory=_empty_results)
    schema_version: int = RESULT_SCHEMA_VERSION

    def write(self, path: Path) -> None:
        """Persist the lane results as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _str(value: object, *, path: str) -> str:
    if not isinstance(value, str):
        raise FeatureVerificationError(f"{path} must be a string")
    return value


def load_lane_results(path: Path) -> LaneResults:
    """Load one lane result file."""
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise FeatureVerificationError(f"{path} must hold a JSON object")
    document = cast(Mapping[str, object], payload)
    if document.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise FeatureVerificationError(f"{path} has an unsupported schema_version")
    raw_results = document.get("results")
    if not isinstance(raw_results, Sequence):
        raise FeatureVerificationError(f"{path}.results must be a list")
    results: list[FeatureResult] = []
    for index, raw in enumerate(cast(Sequence[object], raw_results)):
        if not isinstance(raw, Mapping):
            raise FeatureVerificationError(f"{path}.results[{index}] must be an object")
        entry = cast(Mapping[str, object], raw)
        outcome = _str(entry.get("outcome"), path=f"{path}.results[{index}].outcome")
        if outcome not in TEST_OUTCOMES:
            raise FeatureVerificationError(f"{path}.results[{index}] has outcome {outcome!r}")
        duration = entry.get("duration_seconds", 0.0)
        results.append(
            FeatureResult(
                feature=_str(entry.get("feature"), path=f"{path}.results[{index}].feature"),
                test=_str(entry.get("test"), path=f"{path}.results[{index}].test"),
                outcome=cast(TestOutcome, outcome),
                duration_seconds=float(duration) if isinstance(duration, int | float) else 0.0,
                message=str(entry.get("message", "")),
            )
        )
    raw_versions = document.get("versions", {})
    versions = (
        {str(key): str(value) for key, value in cast(Mapping[object, object], raw_versions).items()}
        if isinstance(raw_versions, Mapping)
        else {}
    )
    return LaneResults(
        lane=_str(document.get("lane"), path=f"{path}.lane"),
        platform=_str(document.get("platform"), path=f"{path}.platform"),
        profile=_str(document.get("profile"), path=f"{path}.profile"),
        started_at=_str(document.get("started_at"), path=f"{path}.started_at"),
        finished_at=str(document.get("finished_at", "")),
        versions=versions,
        results=results,
    )
