"""Unit coverage for security workflow helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

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
                '        <class filename="src/clawops/strongclaw_bootstrap.py" line-rate="0.27"/>',
                "      </classes>",
                "    </package>",
                "  </packages>",
                "</coverage>",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(CiWorkflowError, match="strongclaw_bootstrap.py"):
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


def test_run_channels_runtime_smoke_succeeds_with_deterministic_runtime_checks(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Runtime smoke should use rendered allowlists when overlay sender IDs are placeholders."""
    overlay_path = tmp_path / "platform" / "configs" / "openclaw" / "30-channels.json5"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(
        "\n".join(
            [
                "{",
                '  "channels": {',
                '    "defaults": {"groupPolicy": "allowlist"},',
                '    "telegram": {',
                '      "allowFrom": ["tg:__OWNER_TELEGRAM_ID__"],',
                '      "botToken": {"id": "TELEGRAM_BOT_TOKEN", "source": "env", "provider": "default"},',
                '      "dmPolicy": "pairing",',
                '      "groupPolicy": "allowlist"',
                "    },",
                '    "whatsapp": {',
                '      "allowFrom": ["+5511888888888"],',
                '      "dmPolicy": "pairing",',
                '      "groupPolicy": "allowlist",',
                '      "groupAllowFrom": []',
                "    }",
                "  }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    allowlists_path = tmp_path / "platform" / "configs" / "source-allowlists.example.yaml"
    allowlists_path.write_text(
        "\n".join(
            [
                "telegram_allow:",
                '  - "12345678"',
                "whatsapp_allow:",
                '  - "+5511999999999"',
                "telegram_models:",
                '  "12345678": "messaging"',
                "whatsapp_models:",
                '  "+5511999999999": "messaging"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    artifact_path = tmp_path / "channels-runtime-smoke.json"
    test_context.env.set("STRONGCLAW_CHANNELS_RUNTIME_TELEGRAM_BOT_TOKEN", "token-from-smoke-env")

    ci_workflows.run_channels_runtime_smoke(repo_root=tmp_path, artifact_path=artifact_path)

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["channels_runtime_smoke"] == "pass"
    events = cast(dict[str, object], payload["events"])
    assert cast(dict[str, object], events["telegram_allowlisted_dm"])["accepted"] is True
    assert cast(dict[str, object], events["telegram_pairing_dm"])["decision"] == "pairing_required"
    assert cast(dict[str, object], events["whatsapp_group_allowlist_block"])["decision"] == (
        "group_allowlist_blocked"
    )


def test_run_channels_runtime_smoke_rejects_missing_auth_material(
    tmp_path: Path,
    test_context: TestContext,
) -> None:
    """Runtime smoke should fail when required Telegram auth material is unavailable."""
    overlay_path = tmp_path / "platform" / "configs" / "openclaw" / "30-channels.json5"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(
        "\n".join(
            [
                "{",
                '  "channels": {',
                '    "defaults": {"groupPolicy": "allowlist"},',
                '    "telegram": {"allowFrom": ["tg:12345678"], "botToken": {"id": "TELEGRAM_BOT_TOKEN", "source": "env"}, "dmPolicy": "pairing", "groupPolicy": "allowlist"},',
                '    "whatsapp": {"allowFrom": ["+5511999999999"], "dmPolicy": "pairing", "groupPolicy": "allowlist", "groupAllowFrom": []}',
                "  }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    allowlists_path = tmp_path / "platform" / "configs" / "source-allowlists.example.yaml"
    allowlists_path.write_text(
        'telegram_allow:\n  - "12345678"\nwhatsapp_allow:\n  - "+5511999999999"\n',
        encoding="utf-8",
    )
    test_context.env.remove("TELEGRAM_BOT_TOKEN")
    test_context.env.remove("STRONGCLAW_CHANNELS_RUNTIME_TELEGRAM_BOT_TOKEN")

    with pytest.raises(CiWorkflowError, match="telegram auth material was not loaded"):
        ci_workflows.run_channels_runtime_smoke(repo_root=tmp_path)


def test_run_recovery_smoke_executes_cli_and_fallback_modes_when_openclaw_available(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Recovery smoke should execute backup, verify, and restore in both modes."""
    seen_openclaw_resolution: list[str | None] = []
    archive_path = tmp_path / "archive.tar.gz"

    def fake_which(command: str, *_args: object, **_kwargs: object) -> str | None:
        if command == "openclaw":
            return "/usr/local/bin/openclaw"
        return None

    def fake_create_backup(*, home_dir: Path) -> Path:
        del home_dir
        seen_openclaw_resolution.append(security_helpers.recovery_helpers.shutil.which("openclaw"))
        archive_path.write_text("archive", encoding="utf-8")
        return archive_path

    def fake_verify_backup(target: Path, *, home_dir: Path) -> Path:
        del home_dir
        seen_openclaw_resolution.append(security_helpers.recovery_helpers.shutil.which("openclaw"))
        return target

    def fake_restore_backup(target: Path, *, destination: Path, home_dir: Path) -> Path:
        del home_dir, target
        seen_openclaw_resolution.append(security_helpers.recovery_helpers.shutil.which("openclaw"))
        marker = destination / ".openclaw" / "logs" / "smoke.log"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("restored\n", encoding="utf-8")
        return destination

    test_context.patch.patch_object(
        security_helpers.recovery_helpers.shutil,
        "which",
        new=fake_which,
    )
    test_context.patch.patch_object(security_helpers, "create_backup", new=fake_create_backup)
    test_context.patch.patch_object(security_helpers, "verify_backup", new=fake_verify_backup)
    test_context.patch.patch_object(security_helpers, "restore_backup", new=fake_restore_backup)

    ci_workflows.run_recovery_smoke_with_modes(tmp_root=tmp_path, require_openclaw_cli=False)

    assert seen_openclaw_resolution == [
        "/usr/local/bin/openclaw",
        "/usr/local/bin/openclaw",
        "/usr/local/bin/openclaw",
        None,
        None,
        None,
    ]


def test_run_recovery_smoke_requires_openclaw_cli_when_requested(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Strict mode should fail when OpenClaw CLI cannot be exercised."""

    def fake_which(command: str, *_args: object, **_kwargs: object) -> str | None:
        del command
        return None

    test_context.patch.patch_object(
        security_helpers.recovery_helpers.shutil,
        "which",
        new=fake_which,
    )
    with pytest.raises(CiWorkflowError, match="openclaw-cli recovery smoke was required"):
        ci_workflows.run_recovery_smoke_with_modes(tmp_root=tmp_path, require_openclaw_cli=True)


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


def test_security_workflow_main_dispatches_run_channels_runtime_smoke(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch channels runtime smoke execution."""
    seen_calls: list[tuple[Path, Path | None]] = []

    def fake_run_channels_runtime_smoke(*, repo_root: Path, artifact_path: Path | None) -> None:
        seen_calls.append((repo_root, artifact_path))

    test_context.patch.patch_object(
        security_workflow_script,
        "run_channels_runtime_smoke",
        new=fake_run_channels_runtime_smoke,
    )

    artifact_path = tmp_path / "channels-runtime-smoke.json"
    exit_code = security_workflow_script.main(
        [
            "run-channels-runtime-smoke",
            "--repo-root",
            str(tmp_path),
            "--artifact-path",
            str(artifact_path),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [(tmp_path.resolve(), artifact_path.resolve())]


def test_security_workflow_main_dispatches_run_recovery_smoke(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch recovery smoke execution."""
    seen_calls: list[tuple[Path, bool]] = []

    def fake_run_recovery_smoke_with_modes(*, tmp_root: Path, require_openclaw_cli: bool) -> None:
        seen_calls.append((tmp_root, require_openclaw_cli))

    test_context.patch.patch_object(
        security_workflow_script,
        "run_recovery_smoke_with_modes",
        new=fake_run_recovery_smoke_with_modes,
    )

    exit_code = security_workflow_script.main(
        [
            "run-recovery-smoke",
            "--tmp-root",
            str(tmp_path),
            "--require-openclaw-cli",
        ]
    )

    assert exit_code == 0
    assert seen_calls == [(tmp_path.resolve(), True)]


def test_enforce_independent_review_rejects_missing_non_author_approval(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Critical PR changes should require at least one non-author approval."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 42,
                    "user": {"login": "author-user"},
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_paginated_get(*, url: str, token: str) -> list[dict[str, object]]:
        assert token == "ghs_test"
        if "/files?" in url:
            return [{"filename": "src/clawops/strongclaw_model_auth.py"}]
        if "/reviews?" in url:
            return [{"state": "APPROVED", "user": {"login": "author-user"}}]
        if "/collaborators?" in url:
            return [
                {"login": "author-user", "permissions": {"admin": True}},
                {"login": "reviewer-one", "permissions": {"push": True}},
            ]
        raise AssertionError(url)

    test_context.patch.patch_object(
        security_helpers,
        "_github_paginated_get",
        new=fake_paginated_get,
    )

    with pytest.raises(CiWorkflowError, match="independent review required"):
        security_helpers.enforce_independent_review(
            event_path=event_path,
            repository="example/repo",
            github_token="ghs_test",
        )


def test_enforce_independent_review_accepts_independent_approval(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Security-critical PR changes should pass when an independent reviewer approved."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 43,
                    "user": {"login": "author-user"},
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_paginated_get(*, url: str, token: str) -> list[dict[str, object]]:
        assert token == "ghs_test"
        if "/files?" in url:
            return [{"filename": ".github/workflows/security.yml"}]
        if "/reviews?" in url:
            return [
                {"state": "COMMENTED", "user": {"login": "author-user"}},
                {"state": "APPROVED", "user": {"login": "independent-reviewer"}},
            ]
        raise AssertionError(url)

    test_context.patch.patch_object(
        security_helpers,
        "_github_paginated_get",
        new=fake_paginated_get,
    )

    security_helpers.enforce_independent_review(
        event_path=event_path,
        repository="example/repo",
        github_token="ghs_test",
    )


def test_enforce_independent_review_allows_single_maintainer_repo(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Critical changes should not deadlock when no independent reviewer exists."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 44,
                    "user": {"login": "author-user"},
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_paginated_get(*, url: str, token: str) -> list[dict[str, object]]:
        assert token == "ghs_test"
        if "/files?" in url:
            return [{"filename": ".github/workflows/ci-gate.yml"}]
        if "/reviews?" in url:
            return [{"state": "APPROVED", "user": {"login": "author-user"}}]
        if "/collaborators?" in url:
            return [{"login": "author-user", "permissions": {"admin": True}}]
        raise AssertionError(url)

    test_context.patch.patch_object(
        security_helpers,
        "_github_paginated_get",
        new=fake_paginated_get,
    )

    security_helpers.enforce_independent_review(
        event_path=event_path,
        repository="example/repo",
        github_token="ghs_test",
    )


def test_security_workflow_main_dispatches_independent_review_enforcement(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch independent review enforcement."""
    event_path = tmp_path / "event.json"
    event_path.write_text("{}", encoding="utf-8")
    seen_calls: list[tuple[Path, str, str, str]] = []

    def fake_enforce_independent_review(
        *,
        event_path: Path,
        repository: str,
        github_token: str,
        github_api_base: str,
    ) -> None:
        seen_calls.append((event_path, repository, github_token, github_api_base))

    test_context.patch.patch_object(
        security_workflow_script,
        "enforce_independent_review",
        new=fake_enforce_independent_review,
    )
    test_context.env.set("GITHUB_TOKEN", "ghs_test")

    exit_code = security_workflow_script.main(
        [
            "enforce-independent-review",
            "--event-path",
            str(event_path),
            "--repository",
            "example/repo",
        ]
    )

    assert exit_code == 0
    assert seen_calls == [
        (
            event_path.resolve(),
            "example/repo",
            "ghs_test",
            "https://api.github.com",
        )
    ]
