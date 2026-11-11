"""Join lane results with the feature matrix into a per-feature verdict report."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tests.utils.helpers._feature_verification.manifest import (
    Feature,
    FeatureManifest,
    load_manifest,
)
from tests.utils.helpers._feature_verification.results import (
    FeatureVerdict,
    LaneResults,
    load_lane_results,
)

LANE_RESULTS_GLOB: Final[str] = "feature-results-*.json"
VERDICTS: Final[tuple[FeatureVerdict, ...]] = ("PASS", "FAIL", "UNVERIFIABLE")
_DETAIL_CHARS: Final[int] = 180


@dataclass(frozen=True, slots=True)
class LaneVerdict:
    """One feature's outcome on one lane."""

    verdict: FeatureVerdict
    outcome: str
    message: str
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """One feature's overall verdict across its lanes."""

    id: str
    category: str
    title: str
    verdict: FeatureVerdict
    verification: str | None
    platforms: list[str]
    lanes: dict[str, LaneVerdict]
    reason: str | None
    limitations: str | None
    test: str | None
    call_sites: list[str]


@dataclass(frozen=True, slots=True)
class LaneSummary:
    """Where one lane ran and which tool versions it verified."""

    platform: str
    profile: str
    reported: bool
    versions: dict[str, str]


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """The full per-feature report."""

    generated_at: str
    lanes: dict[str, LaneSummary]
    counts: dict[str, int]
    categories: dict[str, dict[str, int]]
    features: list[FeatureRow]
    schema_version: int = 1

    @property
    def failures(self) -> int:
        """Return the number of FAIL features."""
        return self.counts["FAIL"]

    def to_json(self) -> str:
        """Serialize the report."""
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def _lane_results(results_dir: Path) -> dict[str, LaneResults]:
    lanes: dict[str, LaneResults] = {}
    for path in sorted(results_dir.rglob(LANE_RESULTS_GLOB)):
        lane = load_lane_results(path)
        lanes[lane.lane] = lane
    return lanes


def _lane_verdict(feature: Feature, lane: LaneResults | None) -> LaneVerdict:
    if lane is None:
        return LaneVerdict("FAIL", "not-run", "lane reported no results")
    match = next((result for result in lane.results if result.feature == feature.feature_id), None)
    if match is None:
        return LaneVerdict("FAIL", "not-run", "test did not run on lane")
    return LaneVerdict(
        verdict=match.verdict,
        outcome=match.outcome,
        message=match.message,
        duration_seconds=round(match.duration_seconds, 3),
    )


def _feature_verdict(feature: Feature, lanes: dict[str, LaneVerdict]) -> FeatureVerdict:
    if feature.status == "unverifiable":
        return "UNVERIFIABLE"
    verdicts = {entry.verdict for entry in lanes.values()}
    if "FAIL" in verdicts:
        return "FAIL"
    return "PASS" if verdicts == {"PASS"} else "UNVERIFIABLE"


def build_report(manifest: FeatureManifest, lanes: dict[str, LaneResults]) -> VerificationReport:
    """Return the report for every manifest feature."""
    rows: list[FeatureRow] = []
    counts: Counter[str] = Counter()
    by_category: dict[str, Counter[str]] = {}
    for feature in manifest.features:
        lane_verdicts = {
            lane_id: _lane_verdict(feature, lanes.get(lane_id)) for lane_id in feature.lanes
        }
        verdict = _feature_verdict(feature, lane_verdicts)
        counts[verdict] += 1
        by_category.setdefault(feature.category, Counter())[verdict] += 1
        rows.append(
            FeatureRow(
                id=feature.feature_id,
                category=feature.category,
                title=feature.title,
                verdict=verdict,
                verification=feature.verification,
                platforms=list(manifest.platforms(feature)),
                lanes=lane_verdicts,
                reason=feature.reason,
                limitations=feature.limitations,
                test=feature.test,
                call_sites=list(feature.call_sites),
            )
        )
    return VerificationReport(
        generated_at=datetime.now(tz=UTC).isoformat(),
        lanes={
            lane_id: LaneSummary(
                platform=lane.platform,
                profile=lane.profile,
                reported=lane_id in lanes,
                versions=dict(lanes[lane_id].versions) if lane_id in lanes else {},
            )
            for lane_id, lane in manifest.lanes.items()
        },
        counts={verdict: counts.get(verdict, 0) for verdict in VERDICTS},
        categories={
            category: {verdict: tally.get(verdict, 0) for verdict in VERDICTS}
            for category, tally in sorted(by_category.items())
        },
        features=rows,
    )


def _cell(text: str) -> str:
    flattened = " ".join(text.split())
    if len(flattened) > _DETAIL_CHARS:
        flattened = flattened[: _DETAIL_CHARS - 3] + "..."
    return flattened.replace("|", "\\|")


def _detail(row: FeatureRow) -> str:
    if row.verdict == "UNVERIFIABLE" and row.reason:
        return _cell(row.reason)
    for lane_id, entry in row.lanes.items():
        if entry.verdict != "PASS":
            return _cell(f"{lane_id}: {entry.message}")
    return _cell(row.limitations or "")


def render_markdown(report: VerificationReport) -> str:
    """Render the report as a GitHub step-summary section."""
    lines = [
        "## Feature Verification",
        "",
        "| Lane | Platform | Profile | Versions |",
        "| --- | --- | --- | --- |",
    ]
    for lane_id, lane in report.lanes.items():
        versions = ", ".join(f"{name} {value}" for name, value in lane.versions.items())
        lines.append(
            f"| {lane_id} | {lane.platform} | {lane.profile} | {_cell(versions or 'not reported')} |"
        )
    lines.extend(
        [
            "",
            " · ".join(f"**{verdict}** {report.counts[verdict]}" for verdict in VERDICTS),
            "",
            "| Category | PASS | FAIL | UNVERIFIABLE |",
            "| --- | --- | --- | --- |",
        ]
    )
    for category, tally in report.categories.items():
        lines.append(
            f"| {category} | {tally['PASS']} | {tally['FAIL']} | {tally['UNVERIFIABLE']} |"
        )
    lines.extend(
        [
            "",
            "| Feature | Category | Verdict | Verification | Platforms | Detail |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report.features:
        lines.append(
            f"| `{row.id}` | {row.category} | {row.verdict} | {row.verification or '-'} "
            f"| {', '.join(row.platforms) or '-'} | {_detail(row)} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_lane_report(
    *,
    results_dir: Path,
    manifest_path: Path,
    output_path: Path,
    summary_file: Path | None,
) -> int:
    """Write the JSON report (and optional step summary); return the FAIL count."""
    report = build_report(load_manifest(manifest_path), _lane_results(results_dir))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json(), encoding="utf-8")
    if summary_file is not None:
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        with summary_file.open("a", encoding="utf-8") as handle:
            handle.write(render_markdown(report))
    return report.failures
