"""Collect pytest outcomes for one lane and map them onto manifest features."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from clawops.strongclaw_runtime import varlock_local_env_file
from tests.utils.helpers._feature_verification.envvars import (
    CONTEXT_ENV,
    LANE_ENV,
    MANIFEST_ENV,
    RESULTS_ENV,
)
from tests.utils.helpers._feature_verification.host import default_manifest_path
from tests.utils.helpers._feature_verification.manifest import (
    Feature,
    FeatureManifest,
    FeatureVerificationError,
    load_manifest,
)
from tests.utils.helpers._feature_verification.redaction import redact, secret_values
from tests.utils.helpers._feature_verification.results import (
    MAX_MESSAGE_CHARS,
    FeatureResult,
    LaneResults,
    TestOutcome,
)
from tests.utils.helpers.repo import REPO_ROOT

FEATURE_SUITE_PARTS: Final[tuple[str, ...]] = ("suites", "e2e", "feature_verification")


def is_feature_suite_item(item: pytest.Item) -> bool:
    """Return whether *item* belongs to the feature-verification suite."""
    parts = Path(str(item.fspath)).resolve().parts
    return any(
        parts[index : index + len(FEATURE_SUITE_PARTS)] == FEATURE_SUITE_PARTS
        for index in range(len(parts))
    )


def _message(report: pytest.TestReport) -> str:
    if report.outcome == "skipped" and isinstance(report.longrepr, tuple):
        return str(report.longrepr[2]).removeprefix("Skipped: ")
    text = report.longreprtext or ""
    return text[-MAX_MESSAGE_CHARS:]


@dataclass(slots=True)
class LaneRecorder:
    """Accumulate one lane's feature outcomes and persist them at session end."""

    manifest: FeatureManifest
    lane_id: str
    results_path: Path
    scheduled: dict[str, Feature] = field(default_factory=lambda: dict[str, Feature]())
    results: dict[str, FeatureResult] = field(default_factory=lambda: dict[str, FeatureResult]())
    versions: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    started_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    @classmethod
    def from_environment(cls) -> LaneRecorder | None:
        """Return a recorder when the lane environment is present, else ``None``."""
        lane_id = os.environ.get(LANE_ENV, "").strip()
        if not lane_id:
            return None
        results_raw = os.environ.get(RESULTS_ENV, "").strip()
        if not results_raw or not os.environ.get(CONTEXT_ENV, "").strip():
            raise FeatureVerificationError(
                f"{LANE_ENV} is set, so {RESULTS_ENV} and {CONTEXT_ENV} are required"
            )
        manifest_path = Path(
            os.environ.get(MANIFEST_ENV, "").strip() or default_manifest_path(REPO_ROOT).as_posix()
        )
        manifest = load_manifest(manifest_path)
        if lane_id not in manifest.lanes:
            raise FeatureVerificationError(f"unknown lane {lane_id!r}")
        return cls(manifest=manifest, lane_id=lane_id, results_path=Path(results_raw))

    def select(self, items: list[pytest.Item]) -> tuple[list[pytest.Item], list[pytest.Item]]:
        """Split suite items into (scheduled on this lane, not scheduled)."""
        by_test = self.manifest.feature_by_test()
        keep: list[pytest.Item] = []
        drop: list[pytest.Item] = []
        for item in items:
            feature = by_test.get(item.nodeid)
            if feature is None:
                raise pytest.UsageError(f"{item.nodeid} is not mapped in the feature matrix")
            if self.lane_id in feature.lanes:
                self.scheduled[item.nodeid] = feature
                keep.append(item)
            else:
                drop.append(item)
        return keep, drop

    def record(self, report: pytest.TestReport) -> None:
        """Fold one phase report into the feature's outcome."""
        feature = self.scheduled.get(report.nodeid)
        if feature is None:
            return
        existing = self.results.get(report.nodeid)
        outcome: TestOutcome | None = None
        if report.when == "call":
            outcome = "passed" if report.passed else "skipped" if report.skipped else "failed"
        elif report.failed:
            outcome = "error"
        elif report.when == "setup" and report.skipped:
            outcome = "skipped"
        if outcome is None:
            if existing is not None:
                existing.duration_seconds += report.duration
            return
        if existing is not None and existing.outcome in {"failed", "error"}:
            existing.duration_seconds += report.duration
            return
        self.results[report.nodeid] = FeatureResult(
            feature=feature.feature_id,
            test=report.nodeid,
            outcome=outcome,
            duration_seconds=report.duration + (existing.duration_seconds if existing else 0.0),
            message=_message(report),
        )

    def write(self) -> LaneResults:
        """Persist lane results, marking scheduled-but-unreported features as not run."""
        lane = self.manifest.lanes[self.lane_id]
        results = list(self.results.values())
        for feature in self.manifest.features_for_lane(self.lane_id):
            if feature.test is not None and feature.test not in self.results:
                results.append(
                    FeatureResult(
                        feature=feature.feature_id,
                        test=feature.test,
                        outcome="not-run",
                        duration_seconds=0.0,
                        message="test was scheduled on this lane but did not run",
                    )
                )
        secrets = secret_values(varlock_local_env_file(REPO_ROOT))
        for result in results:
            result.message = redact(result.message, secrets)
        lane_results = LaneResults(
            lane=self.lane_id,
            platform=lane.platform,
            profile=lane.profile,
            started_at=self.started_at,
            finished_at=datetime.now(tz=UTC).isoformat(),
            versions=dict(self.versions),
            results=sorted(results, key=lambda result: result.feature),
        )
        lane_results.write(self.results_path)
        return lane_results
