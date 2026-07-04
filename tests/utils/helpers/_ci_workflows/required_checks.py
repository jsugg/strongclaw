"""Helpers for trusted required-check policy audits."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked


@dataclass(frozen=True)
class RequiredCheckContext:
    """One required GitHub status/check context."""

    context: str
    app_id: int | None
    workflow: str
    job_id: str
    job_name: str
    ui_label: str


@dataclass(frozen=True)
class RequiredCheckManifest:
    """Validated required-check manifest."""

    repository: str
    branch: str
    strict: bool
    trusted_audit_only: bool
    contexts: tuple[RequiredCheckContext, ...]
    allow_force_pushes: bool
    allow_deletions: bool
    required_conversation_resolution: bool
    enforce_admins: bool
    required_approving_review_count: int
    require_code_owner_reviews: bool


def load_required_check_manifest(path: Path) -> RequiredCheckManifest:
    """Load and validate a required-check manifest file."""
    payload = _read_json_mapping(path)
    repository = _required_string(payload, "repository")
    branch = _required_string(payload, "branch")
    trusted_audit_only = _required_bool(payload, "trustedAuditOnly")
    contexts = tuple(
        _required_check_context(entry, label=f"requiredContexts[{index}]")
        for index, entry in enumerate(_required_list(payload, "requiredContexts"))
    )
    if not contexts:
        raise CiWorkflowError("required-check manifest must define at least one context")

    branch_protection = _required_mapping(payload, "branchProtection")
    status_checks = _required_mapping(branch_protection, "requiredStatusChecks")
    strict = _required_bool(status_checks, "strict")
    manifest_context_names = tuple(context.context for context in contexts)
    status_context_names = tuple(
        _string_list_item(item, label="branchProtection.requiredStatusChecks.contexts")
        for item in _required_list(status_checks, "contexts")
    )
    if status_context_names != manifest_context_names:
        raise CiWorkflowError(
            "branchProtection.requiredStatusChecks.contexts must match requiredContexts"
        )

    reviews = _required_mapping(branch_protection, "pullRequestReviews")
    return RequiredCheckManifest(
        repository=repository,
        branch=branch,
        strict=strict,
        trusted_audit_only=trusted_audit_only,
        contexts=contexts,
        allow_force_pushes=_required_bool(branch_protection, "allowForcePushes"),
        allow_deletions=_required_bool(branch_protection, "allowDeletions"),
        required_conversation_resolution=_required_bool(
            branch_protection,
            "requiredConversationResolution",
        ),
        enforce_admins=_required_bool(branch_protection, "enforceAdmins"),
        required_approving_review_count=_required_int(
            reviews,
            "requiredApprovingReviewCount",
        ),
        require_code_owner_reviews=_required_bool(reviews, "requireCodeOwnerReviews"),
    )


def fetch_branch_protection(*, repository: str, branch: str) -> dict[str, object]:
    """Fetch branch-protection JSON with GitHub CLI for a trusted local audit."""
    result = run_checked(
        ["gh", "api", f"repos/{repository}/branches/{branch}/protection"],
        capture_output=True,
    )
    try:
        payload: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CiWorkflowError("GitHub branch-protection response was not valid JSON") from exc
    return _validated_mapping(payload, label="branch protection response")


def load_branch_protection(path: Path) -> dict[str, object]:
    """Load branch-protection JSON from a snapshot file."""
    return _read_json_mapping(path)


def audit_branch_protection(
    *,
    manifest: RequiredCheckManifest,
    branch_protection: dict[str, object],
) -> list[str]:
    """Return branch-protection deviations from the required-check manifest."""
    findings: list[str] = []
    required_context_names = [context.context for context in manifest.contexts]

    status_checks = _optional_mapping(branch_protection.get("required_status_checks"))
    if status_checks is None:
        findings.append("required_status_checks missing")
    else:
        _append_if_mismatch(
            findings,
            observed=status_checks.get("strict"),
            expected=manifest.strict,
            label="required_status_checks.strict",
        )
        contexts = _string_items(status_checks.get("contexts"))
        if contexts != required_context_names:
            findings.append(
                "required_status_checks.contexts mismatch: "
                f"expected {required_context_names!r}, got {contexts!r}"
            )
        checks = _check_entries(status_checks.get("checks"))
        check_context_names = [entry["context"] for entry in checks]
        if check_context_names != required_context_names:
            findings.append(
                "required_status_checks.checks contexts mismatch: "
                f"expected {required_context_names!r}, got {check_context_names!r}"
            )
        for context in manifest.contexts:
            observed_app_id = next(
                (entry["app_id"] for entry in checks if entry["context"] == context.context),
                None,
            )
            if context.app_id is not None and observed_app_id != context.app_id:
                findings.append(
                    f"required_status_checks.checks[{context.context}].app_id mismatch: "
                    f"expected {context.app_id!r}, got {observed_app_id!r}"
                )

    _append_enabled_mismatch(
        findings,
        branch_protection=branch_protection,
        key="allow_force_pushes",
        expected=manifest.allow_force_pushes,
    )
    _append_enabled_mismatch(
        findings,
        branch_protection=branch_protection,
        key="allow_deletions",
        expected=manifest.allow_deletions,
    )
    _append_enabled_mismatch(
        findings,
        branch_protection=branch_protection,
        key="required_conversation_resolution",
        expected=manifest.required_conversation_resolution,
    )
    _append_enabled_mismatch(
        findings,
        branch_protection=branch_protection,
        key="enforce_admins",
        expected=manifest.enforce_admins,
    )

    reviews = _optional_mapping(branch_protection.get("required_pull_request_reviews"))
    if reviews is None:
        findings.append("required_pull_request_reviews missing")
    else:
        _append_if_mismatch(
            findings,
            observed=reviews.get("required_approving_review_count"),
            expected=manifest.required_approving_review_count,
            label="required_pull_request_reviews.required_approving_review_count",
        )
        _append_if_mismatch(
            findings,
            observed=reviews.get("require_code_owner_reviews"),
            expected=manifest.require_code_owner_reviews,
            label="required_pull_request_reviews.require_code_owner_reviews",
        )

    return findings


def format_audit_findings(findings: list[str]) -> str:
    """Return a human-readable audit result."""
    if not findings:
        return "required-check policy: PASS"
    lines = ["required-check policy: FAIL"]
    lines.extend(f"- {finding}" for finding in findings)
    return "\n".join(lines)


def _read_json_mapping(path: Path) -> dict[str, object]:
    resolved_path = path.expanduser().resolve()
    try:
        payload: object = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CiWorkflowError(f"invalid JSON in {resolved_path}: {exc}") from exc
    return _validated_mapping(payload, label=resolved_path.as_posix())


def _required_check_context(value: object, *, label: str) -> RequiredCheckContext:
    entry = _validated_mapping(value, label=label)
    app_id_value = entry.get("appId")
    if app_id_value is not None and not isinstance(app_id_value, int):
        raise CiWorkflowError(f"{label}.appId must be an integer or null")
    return RequiredCheckContext(
        context=_required_string(entry, "context"),
        app_id=app_id_value,
        workflow=_required_string(entry, "workflow"),
        job_id=_required_string(entry, "jobId"),
        job_name=_required_string(entry, "jobName"),
        ui_label=_required_string(entry, "uiLabel"),
    )


def _validated_mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CiWorkflowError(f"{label} must be a JSON object")
    result: dict[str, object] = {}
    for key, entry in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise CiWorkflowError(f"{label} must use string keys")
        result[key] = entry
    return result


def _required_mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    if key not in payload:
        raise CiWorkflowError(f"missing required key: {key}")
    return _validated_mapping(payload[key], label=key)


def _optional_mapping(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    return _validated_mapping(value, label="branch protection field")


def _required_list(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise CiWorkflowError(f"{key} must be a list")
    return cast(list[object], value)


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CiWorkflowError(f"{key} must be a non-empty string")
    return value.strip()


def _required_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise CiWorkflowError(f"{key} must be a boolean")
    return value


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise CiWorkflowError(f"{key} must be an integer")
    return value


def _string_list_item(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CiWorkflowError(f"{label} entries must be non-empty strings")
    return value.strip()


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in cast(list[object], value):
        if isinstance(item, str):
            items.append(item)
    return items


def _check_entries(value: object) -> list[dict[str, int | str | None]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, int | str | None]] = []
    for raw_entry in cast(list[object], value):
        if not isinstance(raw_entry, dict):
            continue
        entry = _validated_mapping(
            cast(dict[object, object], raw_entry),
            label="required_status_checks.checks[]",
        )
        context = entry.get("context")
        app_id = entry.get("app_id")
        if isinstance(context, str) and (isinstance(app_id, int) or app_id is None):
            entries.append({"context": context, "app_id": app_id})
    return entries


def _append_enabled_mismatch(
    findings: list[str],
    *,
    branch_protection: dict[str, object],
    key: str,
    expected: bool,
) -> None:
    wrapper = _optional_mapping(branch_protection.get(key))
    observed = None if wrapper is None else wrapper.get("enabled")
    _append_if_mismatch(findings, observed=observed, expected=expected, label=f"{key}.enabled")


def _append_if_mismatch(
    findings: list[str],
    *,
    observed: object,
    expected: object,
    label: str,
) -> None:
    if observed != expected:
        findings.append(f"{label} mismatch: expected {expected!r}, got {observed!r}")
