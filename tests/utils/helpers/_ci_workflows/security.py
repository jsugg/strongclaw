"""Helpers for security workflow scripting."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.utils.helpers._ci_workflows.common import (
    append_github_path,
    download_file,
    extract_tar_member,
    verify_sha256,
)


def append_coverage_summary(coverage_file: Path, summary_file: Path) -> None:
    """Append the line coverage percentage to the GitHub step summary."""
    coverage = float(ET.parse(coverage_file).getroot().attrib["line-rate"]) * 100
    with summary_file.open("a", encoding="utf-8") as handle:
        handle.write(f"Coverage: {coverage:.2f}%\n")


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
