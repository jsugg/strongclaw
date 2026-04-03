"""Helpers for CI gate lane selection and verdict evaluation."""

from __future__ import annotations

import json
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
    fresh_host_coldstart: bool
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
                self.fresh_host_coldstart,
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
    fresh_host_pr_fast: str
    fresh_host_coldstart: str
    security: str


@dataclass(frozen=True, slots=True)
class CiGateEvidence:
    """Changed-file evidence captured for each lane filter."""

    docs_only: tuple[str, ...]
    fresh_host: tuple[str, ...]
    fresh_host_coldstart: tuple[str, ...]
    security: tuple[str, ...]
    harness: tuple[str, ...]
    memory_plugin: tuple[str, ...]
    compatibility_matrix: tuple[str, ...]


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
    fresh_host_coldstart: str,
    security: str,
    harness: str,
    memory_plugin: str,
    compatibility_matrix: str,
) -> CiGateSelection:
    """Construct lane selection from GitHub Actions output strings."""
    return CiGateSelection(
        docs_only=parse_github_boolean(docs_only, label="docs_only"),
        fresh_host=parse_github_boolean(fresh_host, label="fresh_host"),
        fresh_host_coldstart=parse_github_boolean(
            fresh_host_coldstart,
            label="fresh_host_coldstart",
        ),
        security=parse_github_boolean(security, label="security"),
        harness=parse_github_boolean(harness, label="harness"),
        memory_plugin=parse_github_boolean(memory_plugin, label="memory_plugin"),
        compatibility_matrix=parse_github_boolean(
            compatibility_matrix, label="compatibility_matrix"
        ),
    )


def parse_output_file_list(raw_value: str, *, label: str) -> tuple[str, ...]:
    """Parse a dorny/paths-filter `list-files: json` output value."""
    normalized = raw_value.strip()
    if not normalized:
        return ()

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise CiWorkflowError(f"{label} files output is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise CiWorkflowError(f"{label} files output must decode to a list")

    validated: list[str] = []
    for entry in cast(list[object], payload):
        if not isinstance(entry, str):
            raise CiWorkflowError(f"{label} files output must contain only strings")
        validated.append(entry)
    return tuple(validated)


def evidence_from_output_file_lists(
    *,
    docs_only_files: str,
    fresh_host_files: str,
    fresh_host_coldstart_files: str,
    security_files: str,
    harness_files: str,
    memory_plugin_files: str,
    compatibility_matrix_files: str,
) -> CiGateEvidence:
    """Construct changed-file evidence from action outputs."""
    return CiGateEvidence(
        docs_only=parse_output_file_list(docs_only_files, label="docs_only"),
        fresh_host=parse_output_file_list(fresh_host_files, label="fresh_host"),
        fresh_host_coldstart=parse_output_file_list(
            fresh_host_coldstart_files,
            label="fresh_host_coldstart",
        ),
        security=parse_output_file_list(security_files, label="security"),
        harness=parse_output_file_list(harness_files, label="harness"),
        memory_plugin=parse_output_file_list(memory_plugin_files, label="memory_plugin"),
        compatibility_matrix=parse_output_file_list(
            compatibility_matrix_files,
            label="compatibility_matrix",
        ),
    )


def evidence_from_changed_paths(
    *,
    filters: dict[str, tuple[str, ...]],
    changed_paths: tuple[str, ...],
) -> CiGateEvidence:
    """Construct per-lane evidence by evaluating changed paths against filter patterns."""
    lane_names = (
        "docs_only",
        "fresh_host",
        "fresh_host_coldstart",
        "security",
        "harness",
        "memory_plugin",
        "compatibility_matrix",
    )
    matched_by_lane: dict[str, list[str]] = {lane_name: [] for lane_name in lane_names}
    seen_by_lane: dict[str, set[str]] = {lane_name: set() for lane_name in lane_names}
    for path in changed_paths:
        for lane_name in lane_names:
            patterns = filters.get(lane_name)
            if patterns is None:
                continue
            if not _path_matches_filter(path=path, patterns=patterns):
                continue
            if path in seen_by_lane[lane_name]:
                continue
            seen_by_lane[lane_name].add(path)
            matched_by_lane[lane_name].append(path)

    return CiGateEvidence(
        docs_only=tuple(matched_by_lane["docs_only"]),
        fresh_host=tuple(matched_by_lane["fresh_host"]),
        fresh_host_coldstart=tuple(matched_by_lane["fresh_host_coldstart"]),
        security=tuple(matched_by_lane["security"]),
        harness=tuple(matched_by_lane["harness"]),
        memory_plugin=tuple(matched_by_lane["memory_plugin"]),
        compatibility_matrix=tuple(matched_by_lane["compatibility_matrix"]),
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
        fresh_host_coldstart=_required_match(matches, "fresh_host_coldstart"),
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
    fresh_host_pr_fast: str,
    fresh_host_coldstart: str,
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
        fresh_host_pr_fast=_validate_job_result(fresh_host_pr_fast, label="fresh_host_pr_fast"),
        fresh_host_coldstart=_validate_job_result(
            fresh_host_coldstart, label="fresh_host_coldstart"
        ),
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
        "fresh_host_pr_fast": (selection.fresh_host, results.fresh_host_pr_fast),
        "fresh_host_coldstart": (
            selection.fresh_host_coldstart,
            results.fresh_host_coldstart,
        ),
        "security": (selection.security, results.security),
    }
    for lane_name, (required, result) in required_lanes.items():
        if required and result != "success":
            failures.append(f"{lane_name} is required but finished with result={result!r}")
        if not required and result in {"failure", "cancelled"}:
            failures.append(f"{lane_name} is optional but finished with result={result!r}")

    return (not failures, tuple(failures))


def render_selection_summary(
    selection: CiGateSelection,
    *,
    evidence: CiGateEvidence | None = None,
) -> str:
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
        f"| fresh_host_coldstart | {selection.fresh_host_coldstart} |",
        f"| security | {selection.security} |",
        f"| any_heavy | {selection.any_heavy} |",
        f"| docs_parity_required | {selection.docs_parity_required} |",
    ]

    lane_rows: tuple[tuple[str, bool, tuple[str, ...]], ...] = (
        ("docs_only", selection.docs_only, evidence.docs_only if evidence else ()),
        ("harness", selection.harness, evidence.harness if evidence else ()),
        (
            "compatibility_matrix",
            selection.compatibility_matrix,
            evidence.compatibility_matrix if evidence else (),
        ),
        ("memory_plugin", selection.memory_plugin, evidence.memory_plugin if evidence else ()),
        ("fresh_host", selection.fresh_host, evidence.fresh_host if evidence else ()),
        (
            "fresh_host_coldstart",
            selection.fresh_host_coldstart,
            evidence.fresh_host_coldstart if evidence else (),
        ),
        ("security", selection.security, evidence.security if evidence else ()),
    )

    lines.append("")
    lines.append("Why these values:")
    for lane_name, required, lane_files in lane_rows:
        if required:
            lines.append(
                f"- `{lane_name}` is `True` because matching changes were detected ({_format_file_examples(lane_files)})."
            )
        else:
            lines.append(
                f"- `{lane_name}` is `False` because no changed files matched this lane filter."
            )

    if selection.docs_parity_required:
        lines.append(
            "- `docs_parity_required` is `True` because the change set is docs-only and no heavy lane was selected."
        )
    elif selection.docs_only:
        lines.append(
            "- `docs_parity_required` is `False` even with `docs_only=True` because heavy lanes are also required for non-doc changes in this PR."
        )
    else:
        lines.append(
            "- `docs_parity_required` is `False` because docs-only classification did not match the change set."
        )

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
        ("fresh_host_pr_fast", selection.fresh_host, results.fresh_host_pr_fast),
        (
            "fresh_host_coldstart",
            selection.fresh_host_coldstart,
            results.fresh_host_coldstart,
        ),
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
    normalized_path = path.strip().removeprefix("./")
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


def _format_file_examples(paths: tuple[str, ...]) -> str:
    if not paths:
        return "the matching files list was empty"
    preview = ", ".join(f"`{path}`" for path in paths[:3])
    if len(paths) > 3:
        preview = f"{preview}, and {len(paths) - 3} more"
    return preview
