"""Helpers for security workflow scripting."""

from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final, cast

import clawops.strongclaw_recovery as recovery_helpers
from clawops.allowlist_sync import (
    load_source,
    normalize_telegram,
    normalize_whatsapp,
    render_fragment,
)
from clawops.common import load_json5
from clawops.platform_verify import verify_channels
from clawops.strongclaw_recovery import create_backup, restore_backup, verify_backup
from tests.utils.helpers._ci_workflows.common import (
    CiWorkflowError,
    append_github_path,
    download_file,
    extract_tar_member,
    verify_sha256,
)

GLOBAL_COVERAGE_THRESHOLD = 75.0
CRITICAL_MODULE_COVERAGE_THRESHOLDS: dict[str, float] = {
    "src/clawops/strongclaw_recovery.py": 80.0,
    "src/clawops/strongclaw_model_auth.py": 55.0,
    "src/clawops/strongclaw_varlock_env.py": 35.0,
    "src/clawops/strongclaw_bootstrap.py": 28.0,
}
_GITHUB_API_BASE: Final[str] = "https://api.github.com"
_CRITICAL_REVIEW_PATH_PATTERNS: Final[tuple[str, ...]] = (
    ".github/workflows/**",
    ".github/ci/**",
    "security/**",
    "src/clawops/strongclaw_model_auth.py",
    "src/clawops/strongclaw_varlock_env.py",
    "src/clawops/strongclaw_bootstrap.py",
    "src/clawops/credential_broker.py",
    "platform/docs/SECURITY_MODEL.md",
    "platform/docs/SECRETS_AND_ENV.md",
    "platform/docs/BROWSER_LAB.md",
    "platform/compose/docker-compose.browser-lab*.yaml",
    "platform/compose/docker-compose.browser-lab.*.yaml",
    "platform/workers/browser-lab/**",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "platform/plugins/**/package.json",
    "platform/plugins/**/package-lock.json",
)
_CHANNELS_RUNTIME_TOKEN_ENV_FALLBACK: Final[str] = "STRONGCLAW_CHANNELS_RUNTIME_TELEGRAM_BOT_TOKEN"


def append_coverage_summary(coverage_file: Path, summary_file: Path) -> None:
    """Append the line coverage percentage to the GitHub step summary."""
    coverage = float(ET.parse(coverage_file).getroot().attrib["line-rate"]) * 100
    with summary_file.open("a", encoding="utf-8") as handle:
        handle.write(f"Coverage: {coverage:.2f}%\n")


def enforce_coverage_thresholds(
    coverage_file: Path,
    *,
    global_threshold: float = GLOBAL_COVERAGE_THRESHOLD,
    module_thresholds: Mapping[str, float] = CRITICAL_MODULE_COVERAGE_THRESHOLDS,
) -> None:
    """Raise when overall or critical-module coverage drops below the policy floor."""
    root = ET.parse(coverage_file).getroot()
    overall_coverage = float(root.attrib["line-rate"]) * 100
    if overall_coverage < global_threshold:
        raise CiWorkflowError(
            f"overall line coverage {overall_coverage:.2f}% is below the "
            f"required {global_threshold:.2f}% floor"
        )

    class_coverages: dict[str, float] = {}
    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        line_rate = class_node.attrib.get("line-rate")
        if filename is None or line_rate is None:
            continue
        class_coverages[filename] = float(line_rate) * 100

    for module_path, threshold in module_thresholds.items():
        coverage = _match_module_coverage(class_coverages, module_path)
        if coverage is None:
            raise CiWorkflowError(f"coverage.xml does not contain module {module_path}")
        if coverage < threshold:
            raise CiWorkflowError(
                f"line coverage for {module_path} is {coverage:.2f}% which is below "
                f"the required {threshold:.2f}% floor"
            )


def _match_module_coverage(
    class_coverages: Mapping[str, float],
    module_path: str,
) -> float | None:
    """Resolve one module's coverage by exact path, suffix, or basename."""
    module_name = Path(module_path).name
    for filename, coverage in class_coverages.items():
        if filename == module_path or filename.endswith(module_path) or filename == module_name:
            return coverage
    return None


def install_gitleaks(
    *,
    version: str,
    sha256: str,
    runner_temp: Path,
    github_path_file: Path | None = None,
) -> Path:
    """Install the pinned gitleaks binary into the local bin directory."""
    archive_name = f"gitleaks_{version}_linux_x64.tar.gz"
    download_url = (
        f"https://github.com/gitleaks/gitleaks/releases/download/v{version}/{archive_name}"
    )
    install_dir = Path.home() / ".local" / "bin"
    archive_path = download_file(download_url, runner_temp.expanduser().resolve() / archive_name)
    verify_sha256(archive_path, sha256)
    binary_path = extract_tar_member(archive_path, "gitleaks", install_dir / "gitleaks")
    append_github_path(install_dir, github_path_file)
    return binary_path


def install_syft(
    *,
    version: str,
    sha256: str,
    runner_temp: Path,
    github_path_file: Path | None = None,
) -> Path:
    """Install the pinned syft binary into the local bin directory."""
    archive_name = f"syft_{version.removeprefix('v')}_linux_amd64.tar.gz"
    download_url = f"https://github.com/anchore/syft/releases/download/{version}/{archive_name}"
    install_dir = Path.home() / ".local" / "bin"
    archive_path = download_file(download_url, runner_temp.expanduser().resolve() / archive_name)
    verify_sha256(archive_path, sha256)
    binary_path = extract_tar_member(archive_path, "syft", install_dir / "syft")
    append_github_path(install_dir, github_path_file)
    return binary_path


def write_empty_sarif(output_path: Path, *, information_uri: str) -> None:
    """Write the historical empty SARIF placeholder file."""
    payload: dict[str, object] = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "CodeQL",
                        "informationUri": information_uri,
                        "rules": [],
                    }
                },
                "results": [],
            }
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _next_link(link_header: str | None) -> str | None:
    """Extract the pagination next-link URL from one GitHub Link header."""
    if link_header is None:
        return None
    for segment in link_header.split(","):
        parts = [part.strip() for part in segment.split(";")]
        if not parts:
            continue
        if any(part == 'rel="next"' for part in parts[1:]):
            candidate = parts[0]
            if candidate.startswith("<") and candidate.endswith(">"):
                return candidate[1:-1]
    return None


def _github_paginated_get(
    *,
    url: str,
    token: str,
) -> list[dict[str, object]]:
    """Fetch one GitHub API endpoint and follow pagination links."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    collected: list[dict[str, object]] = []
    next_url: str | None = url
    while next_url is not None:
        request = urllib.request.Request(next_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                link_header = response.headers.get("Link")
        except OSError as exc:
            raise CiWorkflowError(f"github api request failed for {next_url}: {exc}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise CiWorkflowError(f"github api returned invalid json for {next_url}") from exc
        if not isinstance(payload, list):
            raise CiWorkflowError(f"github api payload must be a list for {next_url}")
        for row in cast(list[object], payload):
            if not isinstance(row, dict):
                raise CiWorkflowError(f"github api row must be an object for {next_url}")
            normalized_row: dict[str, object] = {}
            for key, value in cast(dict[object, object], row).items():
                if isinstance(key, str):
                    normalized_row[key] = value
            collected.append(normalized_row)
        next_url = _next_link(link_header)
    return collected


def _is_security_critical_path(path: str) -> bool:
    """Return whether a changed file should require independent review."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in _CRITICAL_REVIEW_PATH_PATTERNS)


def _has_write_permissions(row: Mapping[str, object]) -> bool:
    """Return whether one collaborators API row has write-capable permissions."""
    permissions_value = row.get("permissions")
    if not isinstance(permissions_value, dict):
        return False
    permissions = cast(dict[str, object], permissions_value)
    for key in ("admin", "maintain", "push", "write"):
        if permissions.get(key) is True:
            return True
    return False


def _list_independent_reviewer_candidates(
    *,
    repository: str,
    token: str,
    author_login: str,
    github_api_base: str,
) -> tuple[str, ...]:
    """Return non-author collaborators that can approve pull requests."""
    collaborators = _github_paginated_get(
        url=f"{github_api_base.rstrip('/')}/repos/{repository}/collaborators?per_page=100",
        token=token,
    )
    candidates: set[str] = set()
    for row in collaborators:
        user = row.get("login")
        if not isinstance(user, str):
            continue
        if user == author_login:
            continue
        if not _has_write_permissions(row):
            continue
        candidates.add(user)
    return tuple(sorted(candidates))


def enforce_independent_review(
    *,
    event_path: Path,
    repository: str,
    github_token: str,
    github_api_base: str = _GITHUB_API_BASE,
) -> None:
    """Require at least one non-author approval when critical files change on a PR."""
    if not github_token.strip():
        raise CiWorkflowError("GITHUB_TOKEN is required for independent review enforcement")
    event_payload = json.loads(event_path.read_text(encoding="utf-8"))
    if not isinstance(event_payload, dict):
        raise CiWorkflowError("github event payload must be a JSON object")
    event_payload_obj = cast(dict[str, object], event_payload)
    pull_request = event_payload_obj.get("pull_request")
    if not isinstance(pull_request, dict):
        return
    pull_request_obj = cast(dict[str, object], pull_request)
    number_value = pull_request_obj.get("number")
    if not isinstance(number_value, int):
        raise CiWorkflowError("pull_request.number missing from github event payload")
    user_value = pull_request_obj.get("user")
    if not isinstance(user_value, dict):
        raise CiWorkflowError("pull_request.user missing from github event payload")
    user_obj = cast(dict[str, object], user_value)
    author_login = user_obj.get("login")
    if not isinstance(author_login, str) or not author_login.strip():
        raise CiWorkflowError("pull_request.user.login missing from github event payload")

    api_base = github_api_base.rstrip("/")
    files = _github_paginated_get(
        url=f"{api_base}/repos/{repository}/pulls/{number_value}/files?per_page=100",
        token=github_token,
    )
    changed_paths: list[str] = []
    for row in files:
        filename = row.get("filename")
        if isinstance(filename, str):
            changed_paths.append(filename)
    critical_paths = sorted({path for path in changed_paths if _is_security_critical_path(path)})
    if not critical_paths:
        return

    reviews = _github_paginated_get(
        url=f"{api_base}/repos/{repository}/pulls/{number_value}/reviews?per_page=100",
        token=github_token,
    )
    latest_review_state: dict[str, str] = {}
    for row in reviews:
        user = row.get("user")
        if not isinstance(user, dict):
            continue
        user_obj = cast(dict[str, object], user)
        reviewer_login = user_obj.get("login")
        state = row.get("state")
        if not isinstance(reviewer_login, str) or not isinstance(state, str):
            continue
        latest_review_state[reviewer_login] = state.upper()

    independent_approvals = sorted(
        reviewer
        for reviewer, state in latest_review_state.items()
        if reviewer != author_login and state == "APPROVED"
    )
    if independent_approvals:
        return

    reviewer_candidates = _list_independent_reviewer_candidates(
        repository=repository,
        token=github_token,
        author_login=author_login,
        github_api_base=github_api_base,
    )
    if not reviewer_candidates:
        return

    changed_summary = ", ".join(critical_paths)
    raise CiWorkflowError(
        "independent review required for security-critical changes. "
        f"author={author_login}; changed={changed_summary}; "
        f"candidate_reviewers={', '.join(reviewer_candidates)}; "
        "no non-author APPROVED review found"
    )


def verify_channels_contract(*, repo_root: Path) -> None:
    """Fail when the shipped channels/doc/allowlist contract drifts."""
    resolved_root = repo_root.expanduser().resolve()
    report = verify_channels(
        overlay_path=resolved_root / "platform/configs/openclaw/30-channels.json5",
        channels_doc_path=resolved_root / "platform/docs/CHANNELS.md",
        telegram_guidance_path=resolved_root / "platform/docs/channels/telegram.md",
        whatsapp_guidance_path=resolved_root / "platform/docs/channels/whatsapp.md",
        allowlist_source_path=resolved_root / "platform/configs/source-allowlists.example.yaml",
    )
    if report.ok:
        return

    failed_checks = [check for check in report.checks if not check.ok]
    if not failed_checks:
        raise CiWorkflowError("channel contract verification failed without explicit checks")
    detail = "; ".join(f"{check.name}: {check.message}" for check in failed_checks)
    raise CiWorkflowError(f"channel contract drift detected: {detail}")


def run_channels_runtime_smoke(*, repo_root: Path, artifact_path: Path | None = None) -> None:
    """Exercise deterministic live-like channel runtime checks."""
    resolved_root = repo_root.expanduser().resolve()
    overlay_path = resolved_root / "platform" / "configs" / "openclaw" / "30-channels.json5"
    allowlist_source_path = (
        resolved_root / "platform" / "configs" / "source-allowlists.example.yaml"
    )
    if not overlay_path.is_file():
        raise CiWorkflowError(f"channels runtime overlay missing: {overlay_path}")
    if not allowlist_source_path.is_file():
        raise CiWorkflowError(f"channels allowlist source missing: {allowlist_source_path}")

    overlay_payload = _as_str_object_dict(
        load_json5(overlay_path, allow_duplicate_keys=False),
        path=str(overlay_path),
    )
    channels_payload = _as_str_object_dict(
        overlay_payload.get("channels"),
        path="channels",
    )
    telegram_payload = _as_str_object_dict(
        channels_payload.get("telegram"),
        path="channels.telegram",
    )
    whatsapp_payload = _as_str_object_dict(
        channels_payload.get("whatsapp"),
        path="channels.whatsapp",
    )

    token_spec = _as_str_object_dict(
        telegram_payload.get("botToken"),
        path="channels.telegram.botToken",
    )
    token_env_id = _as_required_string(token_spec.get("id"), path="channels.telegram.botToken.id")
    token_source = _as_required_string(
        token_spec.get("source"), path="channels.telegram.botToken.source"
    )
    if token_source != "env":
        raise CiWorkflowError(
            "channels.telegram.botToken.source must be env for runtime smoke validation"
        )
    token_value = (
        os.environ.get(token_env_id, "").strip()
        or os.environ.get(_CHANNELS_RUNTIME_TOKEN_ENV_FALLBACK, "").strip()
    )
    if not token_value:
        raise CiWorkflowError(
            "telegram auth material was not loaded: set "
            f"{token_env_id} or {_CHANNELS_RUNTIME_TOKEN_ENV_FALLBACK}"
        )

    rendered_payload = _as_str_object_dict(
        render_fragment(load_source(allowlist_source_path)),
        path="rendered_fragment",
    )
    rendered_channels = _as_str_object_dict(
        rendered_payload.get("channels"),
        path="rendered_fragment.channels",
    )
    rendered_telegram = _as_str_object_dict(
        rendered_channels.get("telegram"),
        path="rendered_fragment.channels.telegram",
    )
    rendered_whatsapp = _as_str_object_dict(
        rendered_channels.get("whatsapp"),
        path="rendered_fragment.channels.whatsapp",
    )
    telegram_allowlist = _as_string_list(
        rendered_telegram.get("allowFrom"),
        path="rendered_fragment.channels.telegram.allowFrom",
    )
    whatsapp_allowlist = _as_string_list(
        rendered_whatsapp.get("allowFrom"),
        path="rendered_fragment.channels.whatsapp.allowFrom",
    )
    if not telegram_allowlist:
        raise CiWorkflowError("telegram allowlist cannot be empty for runtime smoke")
    if not whatsapp_allowlist:
        raise CiWorkflowError("whatsapp allowlist cannot be empty for runtime smoke")
    effective_telegram_payload = _with_allowlist(
        channel_payload=telegram_payload,
        allow_from=telegram_allowlist,
    )
    effective_whatsapp_payload = _with_allowlist(
        channel_payload=whatsapp_payload,
        allow_from=whatsapp_allowlist,
    )

    telegram_allow_event = _simulate_dm_event(
        channel_name="telegram",
        channel_payload=effective_telegram_payload,
        sender=telegram_allowlist[0],
        message="health check",
    )
    telegram_pairing_event = _simulate_dm_event(
        channel_name="telegram",
        channel_payload=effective_telegram_payload,
        sender="99999999",
        message="pair me",
    )
    whatsapp_allow_event = _simulate_dm_event(
        channel_name="whatsapp",
        channel_payload=effective_whatsapp_payload,
        sender=whatsapp_allowlist[0],
        message="status",
    )
    whatsapp_group_event = _simulate_group_event(
        channel_name="whatsapp",
        channel_payload=effective_whatsapp_payload,
        sender="+5511888888888",
        group="+5511777777777",
        message="group ping",
    )

    if not telegram_allow_event["accepted"]:
        raise CiWorkflowError("telegram allowlisted inbound message was not accepted")
    if telegram_allow_event["outbound"] is None:
        raise CiWorkflowError("telegram outbound response path was not exercised")
    if (
        telegram_pairing_event["accepted"]
        or telegram_pairing_event["decision"] != "pairing_required"
    ):
        raise CiWorkflowError("telegram pairing policy enforcement did not trigger as expected")
    if not whatsapp_allow_event["accepted"]:
        raise CiWorkflowError("whatsapp allowlisted inbound message was not accepted")
    if whatsapp_allow_event["outbound"] is None:
        raise CiWorkflowError("whatsapp outbound response path was not exercised")
    if (
        whatsapp_group_event["accepted"]
        or whatsapp_group_event["decision"] != "group_allowlist_blocked"
    ):
        raise CiWorkflowError("whatsapp group allowlist enforcement did not trigger as expected")

    report_payload: dict[str, object] = {
        "channels_runtime_smoke": "pass",
        "auth_material": {
            "telegram_token_env": token_env_id,
            "telegram_token_length": len(token_value),
            "token_loaded_via_fallback_env": not bool(os.environ.get(token_env_id, "").strip()),
        },
        "events": {
            "telegram_allowlisted_dm": telegram_allow_event,
            "telegram_pairing_dm": telegram_pairing_event,
            "whatsapp_allowlisted_dm": whatsapp_allow_event,
            "whatsapp_group_allowlist_block": whatsapp_group_event,
        },
    }
    if artifact_path is None:
        return

    resolved_artifact_path = artifact_path.expanduser().resolve()
    resolved_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_artifact_path.write_text(
        json.dumps(report_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _with_allowlist(
    *,
    channel_payload: Mapping[str, object],
    allow_from: list[str],
) -> dict[str, object]:
    """Return one channel payload with a deterministic allowFrom list."""
    merged = dict(channel_payload)
    merged["allowFrom"] = list(allow_from)
    return merged


def _simulate_dm_event(
    *,
    channel_name: str,
    channel_payload: Mapping[str, object],
    sender: str,
    message: str,
) -> dict[str, object]:
    """Simulate one deterministic DM channel event."""
    normalized_sender = _normalize_sender(channel_name=channel_name, sender=sender)
    allow_from = _as_string_list(channel_payload.get("allowFrom"), path=f"{channel_name}.allowFrom")
    dm_policy = _as_required_string(
        channel_payload.get("dmPolicy"), path=f"{channel_name}.dmPolicy"
    )

    if normalized_sender in allow_from:
        return {
            "channel": channel_name,
            "accepted": True,
            "decision": "allowlisted",
            "inbound": {"sender": normalized_sender, "message": message, "scope": "dm"},
            "outbound": {
                "recipient": normalized_sender,
                "message": f"[{channel_name}] ack: {message}",
            },
        }
    if dm_policy == "pairing":
        return {
            "channel": channel_name,
            "accepted": False,
            "decision": "pairing_required",
            "inbound": {"sender": normalized_sender, "message": message, "scope": "dm"},
            "outbound": None,
        }
    return {
        "channel": channel_name,
        "accepted": False,
        "decision": "dm_rejected",
        "inbound": {"sender": normalized_sender, "message": message, "scope": "dm"},
        "outbound": None,
    }


def _simulate_group_event(
    *,
    channel_name: str,
    channel_payload: Mapping[str, object],
    sender: str,
    group: str,
    message: str,
) -> dict[str, object]:
    """Simulate one deterministic group channel event."""
    normalized_sender = _normalize_sender(channel_name=channel_name, sender=sender)
    normalized_group = _normalize_sender(channel_name=channel_name, sender=group)
    group_policy = _as_required_string(
        channel_payload.get("groupPolicy"),
        path=f"{channel_name}.groupPolicy",
    )
    group_allow_from = _as_string_list(
        channel_payload.get("groupAllowFrom", []),
        path=f"{channel_name}.groupAllowFrom",
    )

    if group_policy == "allowlist" and normalized_group not in group_allow_from:
        return {
            "channel": channel_name,
            "accepted": False,
            "decision": "group_allowlist_blocked",
            "inbound": {
                "sender": normalized_sender,
                "group": normalized_group,
                "message": message,
                "scope": "group",
            },
            "outbound": None,
        }
    return {
        "channel": channel_name,
        "accepted": True,
        "decision": "group_allowed",
        "inbound": {
            "sender": normalized_sender,
            "group": normalized_group,
            "message": message,
            "scope": "group",
        },
        "outbound": {
            "recipient": normalized_group,
            "message": f"[{channel_name}] ack: {message}",
        },
    }


def _normalize_sender(*, channel_name: str, sender: str) -> str:
    """Normalize one sender identifier for channel-policy checks."""
    if channel_name == "telegram":
        return normalize_telegram(sender)
    if channel_name == "whatsapp":
        return normalize_whatsapp(sender)
    raise CiWorkflowError(f"unsupported channel for runtime smoke: {channel_name}")


def _as_required_string(value: object, *, path: str) -> str:
    """Validate one required string payload."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise CiWorkflowError(f"{path} must be a non-empty string")


def _as_str_object_dict(value: object, *, path: str) -> dict[str, object]:
    """Validate a string-keyed dictionary payload."""
    if not isinstance(value, Mapping):
        raise CiWorkflowError(f"{path} must be a mapping")
    validated: dict[str, object] = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise CiWorkflowError(f"{path} contains a non-string key")
        validated[key] = item
    return validated


def _as_string_list(value: object, *, path: str) -> list[str]:
    """Validate a list of non-empty strings."""
    if not isinstance(value, list):
        raise CiWorkflowError(f"{path} must be a list")
    validated: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise CiWorkflowError(f"{path} must contain non-empty strings")
        validated.append(item.strip())
    return validated


def run_recovery_smoke(*, tmp_root: Path) -> None:
    """Exercise backup/verify/restore against a disposable OpenClaw home."""
    run_recovery_smoke_with_modes(tmp_root=tmp_root, require_openclaw_cli=False)


def run_recovery_smoke_with_modes(
    *,
    tmp_root: Path,
    require_openclaw_cli: bool,
) -> None:
    """Exercise recovery in both CLI-preferred and forced-fallback modes."""
    resolved_tmp_root = tmp_root.expanduser().resolve()
    openclaw_available = recovery_helpers.shutil.which("openclaw") is not None
    if openclaw_available:
        _run_recovery_cycle(
            resolved_tmp_root=resolved_tmp_root,
            mode_label="openclaw-cli",
            force_tar_fallback=False,
        )
    elif require_openclaw_cli:
        raise CiWorkflowError(
            "openclaw-cli recovery smoke was required but openclaw is not available in PATH"
        )

    _run_recovery_cycle(
        resolved_tmp_root=resolved_tmp_root,
        mode_label="tar-fallback",
        force_tar_fallback=True,
    )


def _run_recovery_cycle(
    *,
    resolved_tmp_root: Path,
    mode_label: str,
    force_tar_fallback: bool,
) -> None:
    """Run one backup/verify/restore cycle and assert marker restoration."""
    home_dir = resolved_tmp_root / f"recovery-home-{mode_label}"
    state_dir = home_dir / ".openclaw"
    marker_path = state_dir / "logs" / "smoke.log"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(f"recovery smoke marker ({mode_label})\n", encoding="utf-8")
    (state_dir / "settings.json").write_text('{"ok":true}\n', encoding="utf-8")

    recovery_context = (
        _force_tar_fallback_for_recovery() if force_tar_fallback else contextlib.nullcontext()
    )
    with recovery_context:
        archive_path = create_backup(home_dir=home_dir)
        verified_archive = verify_backup(archive_path, home_dir=home_dir)
        restore_destination = resolved_tmp_root / f"recovery-restore-{mode_label}"
        restore_backup(verified_archive, destination=restore_destination, home_dir=home_dir)

    restored_marker = restore_destination / ".openclaw" / "logs" / "smoke.log"
    if not restored_marker.exists():
        raise CiWorkflowError(
            f"recovery smoke failed in mode={mode_label}: restored marker missing"
        )


@contextlib.contextmanager
def _force_tar_fallback_for_recovery() -> Iterator[None]:
    """Temporarily force strongclaw_recovery helpers down the tar fallback path."""
    original_which = recovery_helpers.shutil.which
    recovery_shutil: Any = recovery_helpers.shutil

    def _without_openclaw(
        command: str,
        mode: int = os.F_OK | os.X_OK,
        path: str | None = None,
    ) -> str | None:
        if command == "openclaw":
            return None
        return original_which(command, mode, path)

    recovery_shutil.which = _without_openclaw
    try:
        yield
    finally:
        recovery_shutil.which = original_which
