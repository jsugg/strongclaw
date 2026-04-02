"""Unit coverage for security workflow helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.scripts import security_workflow as security_workflow_script
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import security as security_helpers
from tests.utils.helpers._ci_workflows.common import CiWorkflowError


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


def test_enforce_coverage_thresholds_checks_overall_and_critical_modules(tmp_path: Path) -> None:
    """Coverage threshold enforcement should reject low overall or critical-module coverage."""
    coverage_file = tmp_path / "coverage.xml"
    coverage_file.write_text(
        "\n".join(
            [
                '<coverage line-rate="0.80">',
                "  <packages>",
                '    <package name="clawops">',
                "      <classes>",
                '        <class filename="src/clawops/strongclaw_recovery.py" line-rate="0.81"/>',
                '        <class filename="src/clawops/strongclaw_model_auth.py" line-rate="0.76"/>',
                '        <class filename="src/clawops/strongclaw_varlock_env.py" line-rate="0.75"/>',
                '        <class filename="src/clawops/strongclaw_bootstrap.py" line-rate="0.75"/>',
                "      </classes>",
                "    </package>",
                "  </packages>",
                "</coverage>",
            ]
        ),
        encoding="utf-8",
    )

    ci_workflows.enforce_coverage_thresholds(coverage_file)

    coverage_file.write_text(
        coverage_file.read_text(encoding="utf-8").replace(
            'line-rate="0.80"',
            'line-rate="0.70"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(CiWorkflowError, match="overall line coverage"):
        ci_workflows.enforce_coverage_thresholds(coverage_file)

    coverage_file.write_text(
        "\n".join(
            [
                '<coverage line-rate="0.80">',
                "  <packages>",
                '    <package name="clawops">',
                "      <classes>",
                '        <class filename="src/clawops/strongclaw_recovery.py" line-rate="0.40"/>',
                '        <class filename="src/clawops/strongclaw_model_auth.py" line-rate="0.76"/>',
                '        <class filename="src/clawops/strongclaw_varlock_env.py" line-rate="0.75"/>',
                '        <class filename="src/clawops/strongclaw_bootstrap.py" line-rate="0.75"/>',
                "      </classes>",
                "    </package>",
                "  </packages>",
                "</coverage>",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(CiWorkflowError, match="strongclaw_recovery.py"):
        ci_workflows.enforce_coverage_thresholds(coverage_file)


def test_write_empty_sarif_writes_expected_payload(tmp_path: Path) -> None:
    """The placeholder SARIF payload should preserve the expected schema and category driver."""
    output_path = tmp_path / "empty.sarif"

    ci_workflows.write_empty_sarif(output_path, information_uri="https://example.test/repo")

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert payload["runs"][0]["tool"]["driver"]["name"] == "CodeQL"
    assert payload["runs"][0]["tool"]["driver"]["informationUri"] == "https://example.test/repo"


def test_verify_channels_contract_raises_ci_error_when_report_fails(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Channel contract verification should surface failed checks as CI errors."""

    failed_report = SimpleNamespace(
        ok=False,
        checks=[
            SimpleNamespace(ok=True, name="ok-check", message="ok"),
            SimpleNamespace(ok=False, name="channel-docs-pairing", message="drift"),
        ],
    )

    test_context.patch.patch_object(
        security_helpers,
        "verify_channels",
        return_value=failed_report,
    )

    with pytest.raises(CiWorkflowError, match="channel-docs-pairing"):
        ci_workflows.verify_channels_contract(repo_root=tmp_path)


def test_run_recovery_smoke_executes_backup_verify_restore_flow(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Recovery smoke should execute backup, verify, and restore in sequence."""
    seen_calls: list[tuple[str, Path, Path | None]] = []
    archive_path = tmp_path / "archive.tar.gz"

    def fake_create_backup(*, home_dir: Path) -> Path:
        seen_calls.append(("create", home_dir, None))
        archive_path.write_text("archive", encoding="utf-8")
        return archive_path

    def fake_verify_backup(target: Path, *, home_dir: Path) -> Path:
        seen_calls.append(("verify", home_dir, target))
        return target

    def fake_restore_backup(target: Path, *, destination: Path, home_dir: Path) -> Path:
        seen_calls.append(("restore", home_dir, target))
        marker = destination / ".openclaw" / "logs" / "smoke.log"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("restored\n", encoding="utf-8")
        return destination

    test_context.patch.patch_object(security_helpers, "create_backup", new=fake_create_backup)
    test_context.patch.patch_object(security_helpers, "verify_backup", new=fake_verify_backup)
    test_context.patch.patch_object(security_helpers, "restore_backup", new=fake_restore_backup)

    ci_workflows.run_recovery_smoke(tmp_root=tmp_path)

    assert seen_calls[0][0] == "create"
    assert seen_calls[1][0] == "verify"
    assert seen_calls[2][0] == "restore"


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


def test_security_workflow_main_dispatches_coverage_threshold_enforcement(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch coverage threshold enforcement."""
    seen_calls: list[Path] = []

    def fake_enforce_coverage_thresholds(coverage_file: Path) -> None:
        seen_calls.append(coverage_file)

    test_context.patch.patch_object(
        security_workflow_script,
        "enforce_coverage_thresholds",
        new=fake_enforce_coverage_thresholds,
    )

    exit_code = security_workflow_script.main(
        [
            "enforce-coverage-thresholds",
            "--coverage-file",
            str(tmp_path / "coverage.xml"),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [(tmp_path / "coverage.xml").resolve()]


def test_security_workflow_main_dispatches_verify_channels_contract(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch channel contract verification."""
    seen_calls: list[Path] = []

    def fake_verify_channels_contract(*, repo_root: Path) -> None:
        seen_calls.append(repo_root)

    test_context.patch.patch_object(
        security_workflow_script,
        "verify_channels_contract",
        new=fake_verify_channels_contract,
    )

    exit_code = security_workflow_script.main(
        [
            "verify-channels-contract",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [tmp_path.resolve()]


def test_security_workflow_main_dispatches_run_recovery_smoke(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch recovery smoke execution."""
    seen_calls: list[Path] = []

    def fake_run_recovery_smoke(*, tmp_root: Path) -> None:
        seen_calls.append(tmp_root)

    test_context.patch.patch_object(
        security_workflow_script,
        "run_recovery_smoke",
        new=fake_run_recovery_smoke,
    )

    exit_code = security_workflow_script.main(
        [
            "run-recovery-smoke",
            "--tmp-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [tmp_path.resolve()]
