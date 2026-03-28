"""Unit coverage for security workflow helpers."""

from __future__ import annotations

import json
from pathlib import Path

from tests.plugins.infrastructure.context import TestContext
from tests.scripts import security_workflow as security_workflow_script
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import security as security_helpers


def test_append_coverage_summary_appends_percentage(tmp_path: Path) -> None:
    """Coverage summaries should append a formatted percentage."""
    coverage_file = tmp_path / "coverage.xml"
    coverage_file.write_text('<coverage line-rate="0.875"></coverage>', encoding="utf-8")
    summary_file = tmp_path / "summary.md"

    ci_workflows.append_coverage_summary(coverage_file, summary_file)

    assert summary_file.read_text(encoding="utf-8") == "Coverage: 87.50%\n"


def test_install_gitleaks_downloads_and_extracts_archive(
    test_context: TestContext, tmp_path: Path
) -> None:
    """Pinned gitleaks installation should use the expected tarball metadata."""
    seen_calls: list[tuple[str, object]] = []

    def fake_download_file(url: str, destination: Path) -> Path:
        seen_calls.append(("download", url))
        destination.write_text("archive", encoding="utf-8")
        return destination

    def fake_verify_sha256(path: Path, expected_sha256: str) -> None:
        seen_calls.append(("sha256", (path.name, expected_sha256)))

    def fake_extract_tar_member(archive_path: Path, member_name: str, destination: Path) -> Path:
        seen_calls.append(("extract", (archive_path.name, member_name, destination.name)))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("binary", encoding="utf-8")
        return destination

    def fake_append_github_path(path: Path, github_path_file: Path | None) -> None:
        seen_calls.append(("path", (path, github_path_file)))

    test_context.patch.patch_object(security_helpers, "download_file", new=fake_download_file)
    test_context.patch.patch_object(security_helpers, "verify_sha256", new=fake_verify_sha256)
    test_context.patch.patch_object(
        security_helpers, "extract_tar_member", new=fake_extract_tar_member
    )
    test_context.patch.patch_object(
        security_helpers, "append_github_path", new=fake_append_github_path
    )

    binary_path = ci_workflows.install_gitleaks(
        version="8.28.0",
        sha256="deadbeef",
        runner_temp=tmp_path,
        github_path_file=tmp_path / "github.path",
    )

    assert binary_path.name == "gitleaks"
    assert seen_calls[0] == (
        "download",
        "https://github.com/gitleaks/gitleaks/releases/download/v8.28.0/gitleaks_8.28.0_linux_x64.tar.gz",
    )
    assert ("extract", ("gitleaks_8.28.0_linux_x64.tar.gz", "gitleaks", "gitleaks")) in seen_calls


def test_write_empty_sarif_writes_expected_payload(tmp_path: Path) -> None:
    """The placeholder SARIF payload should preserve the expected schema and category driver."""
    output_path = tmp_path / "empty.sarif"

    ci_workflows.write_empty_sarif(output_path, information_uri="https://example.test/repo")

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert payload["runs"][0]["tool"]["driver"]["name"] == "CodeQL"
    assert payload["runs"][0]["tool"]["driver"]["informationUri"] == "https://example.test/repo"


def test_security_workflow_main_dispatches_write_summary(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch coverage summary generation."""
    seen_calls: list[tuple[Path, Path]] = []

    def fake_append_coverage_summary(coverage_file: Path, summary_file: Path) -> None:
        seen_calls.append((coverage_file, summary_file))

    test_context.patch.patch_object(
        security_workflow_script,
        "append_coverage_summary",
        new=fake_append_coverage_summary,
    )

    exit_code = security_workflow_script.main(
        [
            "write-coverage-summary",
            "--coverage-file",
            str(tmp_path / "coverage.xml"),
            "--summary-file",
            str(tmp_path / "summary.md"),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [
        ((tmp_path / "coverage.xml").resolve(), (tmp_path / "summary.md").resolve())
    ]
