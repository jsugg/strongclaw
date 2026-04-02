"""Helpers for CI gate lane selection and verdict evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import cast

import yaml

from tests.utils.helpers._ci_workflows.common import CiWorkflowError

_ALLOWED_JOB_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})


@dataclass(frozen=True, slots=True)
class CiGateSelection:
    """Boolean lane selection derived from PR path filters."""

    docs_only: bool
    fresh_host: bool
    security: bool
    harness: bool
    memory_plugin: bool
    compatibility_matrix: bool

    @property
    def any_heavy(self) -> bool:
        """Return whether any heavy lane is required."""
        return any(
            (
                self.fresh_host,
                self.security,
                self.harness,
                self.memory_plugin,
                self.compatibility_matrix,
            )
        )

    @property
    def docs_parity_required(self) -> bool:
        """Return whether docs parity is required for this change-set."""
        return self.docs_only and not self.any_heavy


@dataclass(frozen=True, slots=True)
class CiGateResults:
    """Per-job result states consumed by the verdict stage."""

    classify: str
    docs_parity: str
    harness: str
    compatibility_matrix: str
    memory_plugin: str
    fresh_host: str
    security: str


def parse_github_boolean(raw_value: str, *, label: str) -> bool:
    """Parse a GitHub Actions boolean output into a Python bool."""
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise CiWorkflowError(f"{label} must be 'true' or 'false', got {raw_value!r}")


def selection_from_output_flags(
    *,
    docs_only: str,
    fresh_host: str,
    security: str,
    harness: str,
    memory_plugin: str,
    compatibility_matrix: str,
) -> CiGateSelection:
    """Construct lane selection from GitHub Actions output strings."""
    return CiGateSelection(
        docs_only=parse_github_boolean(docs_only, label="docs_only"),
        fresh_host=parse_github_boolean(fresh_host, label="fresh_host"),
        security=parse_github_boolean(security, label="security"),
        harness=parse_github_boolean(harness, label="harness"),
        memory_plugin=parse_github_boolean(memory_plugin, label="memory_plugin"),
        compatibility_matrix=parse_github_boolean(
            compatibility_matrix, label="compatibility_matrix"
        ),
    )


def load_ci_gate_filters(filters_file: Path) -> dict[str, tuple[str, ...]]:
    """Load and validate CI gate filter definitions from YAML."""
    payload_object: object = yaml.safe_load(filters_file.read_text(encoding="utf-8"))
    if not isinstance(payload_object, dict):
        raise CiWorkflowError("CI gate filters file must contain a mapping")

    payload = cast(dict[object, object], payload_object)
    validated_filters: dict[str, tuple[str, ...]] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise CiWorkflowError("CI gate filter keys must be strings")
        if not isinstance(value, list):
            raise CiWorkflowError(f"CI gate filter {key!r} must be a list of glob patterns")

        patterns: list[str] = []
        for entry in cast(list[object], value):
            if not isinstance(entry, str):
                raise CiWorkflowError(
                    f"CI gate filter {key!r} must only contain string glob patterns"
                )
            patterns.append(entry)
        validated_filters[key] = tuple(patterns)

    return validated_filters


def evaluate_filter_matches(
    *,
    filters: dict[str, tuple[str, ...]],
    changed_paths: tuple[str, ...],
) -> dict[str, bool]:
    """Evaluate all filters against a changed path-set."""
    matches: dict[str, bool] = {}
    for filter_name, patterns in filters.items():
        matches[filter_name] = any(
            _path_matches_filter(path=path, patterns=patterns) for path in changed_paths
        )
    return matches


def selection_from_filter_matches(matches: dict[str, bool]) -> CiGateSelection:
    """Construct lane selection from evaluated filter matches."""
    return CiGateSelection(
        docs_only=_required_match(matches, "docs_only"),
        fresh_host=_required_match(matches, "fresh_host"),
        security=_required_match(matches, "security"),
        harness=_required_match(matches, "harness"),
        memory_plugin=_required_match(matches, "memory_plugin"),
        compatibility_matrix=_required_match(matches, "compatibility_matrix"),
    )


def build_results(
    *,
    classify: str,
    docs_parity: str,
    harness: str,
    compatibility_matrix: str,
    memory_plugin: str,
    fresh_host: str,
    security: str,
) -> CiGateResults:
    """Build validated per-job result values for verdict evaluation."""
    return CiGateResults(
        classify=_validate_job_result(classify, label="classify"),
        docs_parity=_validate_job_result(docs_parity, label="docs_parity"),
        harness=_validate_job_result(harness, label="harness"),
        compatibility_matrix=_validate_job_result(
            compatibility_matrix, label="compatibility_matrix"
        ),
        memory_plugin=_validate_job_result(memory_plugin, label="memory_plugin"),
        fresh_host=_validate_job_result(fresh_host, label="fresh_host"),
        security=_validate_job_result(security, label="security"),
    )


def evaluate_verdict(
    *, selection: CiGateSelection, results: CiGateResults
) -> tuple[bool, tuple[str, ...]]:
    """Return verdict success plus a tuple of actionable failure messages."""
    failures: list[str] = []
    if results.classify != "success":
        failures.append("classification stage did not finish successfully")

    required_lanes: dict[str, tuple[bool, str]] = {
        "docs_parity": (selection.docs_parity_required, results.docs_parity),
        "harness": (selection.harness, results.harness),
        "compatibility_matrix": (selection.compatibility_matrix, results.compatibility_matrix),
        "memory_plugin": (selection.memory_plugin, results.memory_plugin),
        "fresh_host": (selection.fresh_host, results.fresh_host),
        "security": (selection.security, results.security),
    }
    for lane_name, (required, result) in required_lanes.items():
        if required and result != "success":
            failures.append(f"{lane_name} is required but finished with result={result!r}")
        if not required and result in {"failure", "cancelled"}:
            failures.append(f"{lane_name} is optional but finished with result={result!r}")

    return (not failures, tuple(failures))


def render_selection_summary(selection: CiGateSelection) -> str:
    """Render a markdown summary of lane requirements."""
    lines = [
        "### CI Gate Classification",
        "",
        "| Lane | Required |",
        "| --- | --- |",
        f"| docs_only | {selection.docs_only} |",
        f"| harness | {selection.harness} |",
        f"| compatibility_matrix | {selection.compatibility_matrix} |",
        f"| memory_plugin | {selection.memory_plugin} |",
        f"| fresh_host | {selection.fresh_host} |",
        f"| security | {selection.security} |",
        f"| any_heavy | {selection.any_heavy} |",
        f"| docs_parity_required | {selection.docs_parity_required} |",
    ]
    return "\n".join(lines)


def render_verdict_summary(
    *,
    selection: CiGateSelection,
    results: CiGateResults,
    failures: tuple[str, ...],
) -> str:
    """Render a markdown summary for the verdict stage."""
    lane_rows = [
        ("classify", True, results.classify),
        ("docs_parity", selection.docs_parity_required, results.docs_parity),
        ("harness", selection.harness, results.harness),
        ("compatibility_matrix", selection.compatibility_matrix, results.compatibility_matrix),
        ("memory_plugin", selection.memory_plugin, results.memory_plugin),
        ("fresh_host", selection.fresh_host, results.fresh_host),
        ("security", selection.security, results.security),
    ]
    lines = [
        "### CI Verdict",
        "",
        "| Job | Required | Result |",
        "| --- | --- | --- |",
    ]
    for lane_name, required, result in lane_rows:
        lines.append(f"| {lane_name} | {required} | {result} |")

    if failures:
        lines.extend(
            (
                "",
                "Failures:",
            )
        )
        lines.extend(f"- {message}" for message in failures)
    else:
        lines.append("")
        lines.append("All required lanes completed successfully.")
    return "\n".join(lines)


def write_github_output(entries: dict[str, str], *, github_output_file: Path | None) -> None:
    """Append key/value outputs to the GitHub Actions output file."""
    if github_output_file is None:
        return
    github_output_file.parent.mkdir(parents=True, exist_ok=True)
    with github_output_file.open("a", encoding="utf-8") as handle:
        for key, value in entries.items():
            handle.write(f"{key}={value}\n")


def write_github_summary(*, markdown: str, github_summary_file: Path | None) -> None:
    """Append markdown text to the GitHub step summary file."""
    if github_summary_file is None:
        return
    github_summary_file.parent.mkdir(parents=True, exist_ok=True)
    with github_summary_file.open("a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


def emit_filters_for_github_output(
    *,
    filters_file: Path,
    github_output_file: Path | None,
) -> None:
    """Emit the filters file content as a multiline GitHub output value."""
    if github_output_file is None:
        return
    payload = filters_file.read_text(encoding="utf-8")
    github_output_file.parent.mkdir(parents=True, exist_ok=True)
    delimiter = "__CI_GATE_FILTERS__"
    with github_output_file.open("a", encoding="utf-8") as handle:
        handle.write(f"filters<<{delimiter}\n")
        handle.write(payload)
        if not payload.endswith("\n"):
            handle.write("\n")
        handle.write(f"{delimiter}\n")


def _required_match(matches: dict[str, bool], key: str) -> bool:
    value = matches.get(key)
    if value is None:
        raise CiWorkflowError(f"missing required filter match value for {key!r}")
    return value


def _validate_job_result(raw_result: str, *, label: str) -> str:
    normalized = raw_result.strip().lower()
    if normalized not in _ALLOWED_JOB_RESULTS:
        raise CiWorkflowError(
            f"{label} result must be one of {sorted(_ALLOWED_JOB_RESULTS)}, got {raw_result!r}"
        )
    return normalized


def _path_matches_filter(*, path: str, patterns: tuple[str, ...]) -> bool:
    normalized_path = path.strip().lstrip("./")
    matched = False
    for pattern in patterns:
        is_exclusion = pattern.startswith("!")
        candidate_pattern = pattern[1:] if is_exclusion else pattern
        if not candidate_pattern:
            continue
        if _glob_match(normalized_path, candidate_pattern):
            matched = not is_exclusion
    return matched


def _glob_match(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[: -len("/**")]
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    if pattern.startswith("**/"):
        return fnmatch(path, pattern) or fnmatch(path, pattern[len("**/") :])
    return fnmatch(path, pattern)
