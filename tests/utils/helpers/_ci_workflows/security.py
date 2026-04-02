"""Helpers for security workflow scripting."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

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
    "src/clawops/strongclaw_model_auth.py": 18.0,
    "src/clawops/strongclaw_varlock_env.py": 19.0,
    "src/clawops/strongclaw_bootstrap.py": 28.0,
}


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


def run_recovery_smoke(*, tmp_root: Path) -> None:
    """Exercise backup/verify/restore against a disposable OpenClaw home."""
    resolved_tmp_root = tmp_root.expanduser().resolve()
    home_dir = resolved_tmp_root / "recovery-home"
    state_dir = home_dir / ".openclaw"
    marker_path = state_dir / "logs" / "smoke.log"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("recovery smoke marker\n", encoding="utf-8")
    (state_dir / "settings.json").write_text('{"ok":true}\n', encoding="utf-8")

    archive_path = create_backup(home_dir=home_dir)
    verified_archive = verify_backup(archive_path, home_dir=home_dir)

    restore_destination = resolved_tmp_root / "recovery-restore"
    restore_backup(verified_archive, destination=restore_destination, home_dir=home_dir)

    restored_marker = restore_destination / ".openclaw" / "logs" / "smoke.log"
    if not restored_marker.exists():
        raise CiWorkflowError(
            "recovery smoke failed: restored marker missing after backup/verify/restore cycle"
        )
