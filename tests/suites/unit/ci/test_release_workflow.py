"""Unit coverage for release workflow helpers."""

from __future__ import annotations

import json
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import release as release_helpers
from tests.utils.helpers.repo import REPO_ROOT


def _copy_required_checks_manifest(repo_root: Path) -> None:
    """Copy the repository policy manifest into a temporary release checkout."""
    destination = repo_root / ".github" / "required-checks.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        (REPO_ROOT / ".github" / "required-checks.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_clean_artifact_directories_removes_paths(tmp_path: Path) -> None:
    """Release cleanup should remove stale build directories."""
    build_dir = tmp_path / "build"
    dist_dir = tmp_path / "dist"
    build_dir.mkdir()
    dist_dir.mkdir()
    (build_dir / "artifact.txt").write_text("build", encoding="utf-8")
    (dist_dir / "artifact.txt").write_text("dist", encoding="utf-8")

    ci_workflows.clean_artifact_directories([build_dir, dist_dir])

    assert not build_dir.exists()
    assert not dist_dir.exists()


def test_verify_release_artifacts_runs_twine_and_smoke_tests(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Release verification should check artifacts and install both wheel and sdist."""
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel_path = dist_dir / "clawops-1.0.0-py3-none-any.whl"
    sdist_path = dist_dir / "clawops-1.0.0.tar.gz"
    wheel_path.write_text("wheel", encoding="utf-8")
    sdist_path.write_text("sdist", encoding="utf-8")
    seen_commands: list[list[str]] = []
    smoke_targets: list[Path] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del cwd, env, timeout_seconds, capture_output
        seen_commands.append(command)
        return None

    def fake_smoke_test(
        venv_dir: Path,
        artifact_path: Path,
        *,
        smoke_workspace_root: Path,
    ) -> None:
        del venv_dir, smoke_workspace_root
        smoke_targets.append(artifact_path)

    def fake_policy_check(artifact_path: Path) -> None:
        del artifact_path

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(
        release_helpers,
        "_install_and_smoke_test",
        new=fake_smoke_test,
    )
    test_context.patch.patch_object(
        release_helpers,
        "_enforce_artifact_content_policy",
        new=fake_policy_check,
    )

    ci_workflows.verify_release_artifacts(dist_dir)

    assert seen_commands[0][:3] == ["uv", "run", "twine"]
    assert smoke_targets == [wheel_path, sdist_path]


def test_publish_github_release_creates_when_missing(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Release publishing should create the release when it does not exist yet."""
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "artifact.whl").write_text("wheel", encoding="utf-8")
    (dist_dir / release_helpers.RELEASE_MANIFEST_NAME).write_text("{}", encoding="utf-8")
    (dist_dir / release_helpers.RELEASE_CHECKSUMS_NAME).write_text(
        "0  artifact.whl\n",
        encoding="utf-8",
    )
    sbom_path = tmp_path / "sbom.spdx.json"
    sbom_path.write_text("{}", encoding="utf-8")
    seen_commands: list[list[str]] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del cwd, env, timeout_seconds, capture_output
        seen_commands.append(command)
        if command[:3] == ["gh", "release", "view"]:
            raise ci_workflows.CiWorkflowError("missing release")
        return None

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)

    ci_workflows.publish_github_release("v1.0.0", dist_dir, sbom_path)

    assert seen_commands[0] == ["gh", "release", "view", "v1.0.0"]
    assert seen_commands[1][:3] == ["gh", "release", "create"]
    assert str(dist_dir / release_helpers.RELEASE_MANIFEST_NAME) in seen_commands[1]
    assert str(dist_dir / release_helpers.RELEASE_CHECKSUMS_NAME) in seen_commands[1]
    assert str(sbom_path) in seen_commands[1]


def test_write_release_metadata_records_artifacts_and_platform_inventory(
    tmp_path: Path,
) -> None:
    """Release metadata should bind artifacts, SBOM, runtime assets, and image digests."""
    repo_root = tmp_path / "repo"
    dist_dir = tmp_path / "dist"
    package_dir = repo_root / "src" / "clawops"
    package_dir.mkdir(parents=True)
    dist_dir.mkdir()
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "1.2.3"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    wheel_path = dist_dir / "clawops-1.2.3-py3-none-any.whl"
    pinned_digest = "a" * 64
    with zipfile.ZipFile(wheel_path, mode="w") as archive:
        archive.writestr("clawops/assets/platform/docs/SECURITY_MODEL.md", "# Security\n")
        archive.writestr(
            "clawops/assets/platform/compose/docker-compose.aux-stack.yaml",
            f"services:\n  db:\n    image: postgres:16-alpine@sha256:{pinned_digest}\n",
        )
    sdist_path = dist_dir / "clawops-1.2.3.tar.gz"
    sdist_path.write_text("sdist", encoding="utf-8")
    sbom_path = tmp_path / "sbom.spdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    manifest_path, checksums_path = ci_workflows.write_release_metadata(
        tag="v1.2.3",
        repo_root=repo_root,
        dist_dir=dist_dir,
        sbom_path=sbom_path,
    )

    payload = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    runtime_assets = cast(dict[str, Any], payload["runtimeAssets"])
    container_images = cast(list[dict[str, Any]], payload["containerImages"])
    checksum_text = checksums_path.read_text(encoding="utf-8")

    assert payload["manifestVersion"] == 1
    assert cast(dict[str, Any], payload["release"])["tag"] == "v1.2.3"
    assert cast(dict[str, Any], payload["installAuthority"])["primaryArtifact"] == wheel_path.name
    assert runtime_assets["sourceArtifact"] == wheel_path.name
    assert runtime_assets["fileCount"] == 2
    assert container_images == [
        {
            "path": "compose/docker-compose.aux-stack.yaml",
            "line": 3,
            "reference": f"postgres:16-alpine@sha256:{pinned_digest}",
            "digest": f"sha256:{pinned_digest}",
            "digestPinned": True,
        }
    ]
    assert wheel_path.name in checksum_text
    assert sdist_path.name in checksum_text
    assert sbom_path.name in checksum_text
    assert release_helpers.RELEASE_MANIFEST_NAME in checksum_text
    assert release_helpers.RELEASE_CHECKSUMS_NAME not in checksum_text


def test_write_release_metadata_rejects_unpinned_container_images(tmp_path: Path) -> None:
    """Release metadata should fail if shipped compose images are not digest-pinned."""
    repo_root = tmp_path / "repo"
    dist_dir = tmp_path / "dist"
    package_dir = repo_root / "src" / "clawops"
    package_dir.mkdir(parents=True)
    dist_dir.mkdir()
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    wheel_path = dist_dir / "clawops-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, mode="w") as archive:
        archive.writestr(
            "clawops/assets/platform/compose/docker-compose.aux-stack.yaml",
            "services:\n  db:\n    image: postgres:16-alpine\n",
        )
    (dist_dir / "clawops-1.2.3.tar.gz").write_text("sdist", encoding="utf-8")
    sbom_path = tmp_path / "sbom.spdx.json"
    sbom_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ci_workflows.CiWorkflowError, match="digest-pinned images"):
        ci_workflows.write_release_metadata(
            tag="v1.2.3",
            repo_root=repo_root,
            dist_dir=dist_dir,
            sbom_path=sbom_path,
        )


def test_release_workflow_main_dispatches_verify_artifacts(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch artifact verification."""
    from tests.scripts import release_workflow as release_workflow_script

    seen_calls: list[Path] = []

    def fake_verify_release_artifacts(dist_dir: Path) -> None:
        seen_calls.append(dist_dir)

    test_context.patch.patch_object(
        release_workflow_script,
        "verify_release_artifacts",
        new=fake_verify_release_artifacts,
    )

    exit_code = release_workflow_script.main(
        ["verify-artifacts", "--dist-dir", str(tmp_path / "dist")]
    )

    assert exit_code == 0
    assert seen_calls == [(tmp_path / "dist").resolve()]


def test_release_workflow_main_dispatches_verify_tag_version(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch tag/version parity verification."""
    from tests.scripts import release_workflow as release_workflow_script

    seen_calls: list[tuple[str, Path]] = []

    def fake_verify_tag_version_parity(*, tag: str, repo_root: Path) -> None:
        seen_calls.append((tag, repo_root))

    test_context.patch.patch_object(
        release_workflow_script,
        "verify_tag_version_parity",
        new=fake_verify_tag_version_parity,
    )

    exit_code = release_workflow_script.main(
        [
            "verify-tag-version",
            "--tag",
            "v0.1.0",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [("v0.1.0", tmp_path.resolve())]


def test_verify_release_tag_preflight_requires_main_and_verdict(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Release tag preflight should require a main-reachable, CI-green tag."""
    (tmp_path / "src" / "clawops").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "clawops" / "__init__.py").write_text(
        '__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    _copy_required_checks_manifest(tmp_path)
    seen_commands: list[list[str]] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del cwd, env, timeout_seconds
        seen_commands.append(command)
        if command[:2] == ["git", "rev-parse"]:
            assert capture_output is True
            return SimpleNamespace(stdout="abc123\n", stderr="")
        if command[:2] == ["gh", "api"]:
            assert capture_output is True
            return SimpleNamespace(stdout="1\n", stderr="")
        return SimpleNamespace(stdout="", stderr="")

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)

    ci_workflows.verify_release_tag_preflight(
        tag="v1.2.3",
        repo_root=tmp_path,
        repository="jsugg/strongclaw",
    )

    assert ["git", "rev-parse", "v1.2.3^{commit}"] in seen_commands
    assert [
        "git",
        "fetch",
        "--no-tags",
        "origin",
        f"+main:{release_helpers.RELEASE_MAIN_REF}",
    ] in seen_commands
    assert [
        "git",
        "merge-base",
        "--is-ancestor",
        "abc123",
        release_helpers.RELEASE_MAIN_REF,
    ] in seen_commands
    assert any(
        command[:2] == ["gh", "api"] and "check-runs" in command[2] for command in seen_commands
    )
    check_command = next(command for command in seen_commands if command[:2] == ["gh", "api"])
    assert ".app.id == 15368" in check_command[-1]


def test_verify_release_tag_preflight_rejects_missing_verdict(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Release tag preflight should fail when Verdict has not succeeded."""
    (tmp_path / "src" / "clawops").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "clawops" / "__init__.py").write_text(
        '__version__ = "1.2.3"\n',
        encoding="utf-8",
    )
    _copy_required_checks_manifest(tmp_path)

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del cwd, env, timeout_seconds, capture_output
        if command[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(stdout="abc123\n", stderr="")
        if command[:2] == ["gh", "api"]:
            return SimpleNamespace(stdout="0\n", stderr="")
        return SimpleNamespace(stdout="", stderr="")

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)

    with pytest.raises(ci_workflows.CiWorkflowError, match="Verdict check run from app 15368"):
        ci_workflows.verify_release_tag_preflight(
            tag="v1.2.3",
            repo_root=tmp_path,
            repository="jsugg/strongclaw",
        )


def test_release_workflow_main_dispatches_runtime_readiness(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch runtime-readiness checks."""
    from tests.scripts import release_workflow as release_workflow_script

    seen_calls: list[Path] = []

    def fake_run_release_runtime_readiness(*, repo_root: Path) -> None:
        seen_calls.append(repo_root)

    test_context.patch.patch_object(
        release_workflow_script,
        "run_release_runtime_readiness",
        new=fake_run_release_runtime_readiness,
    )

    exit_code = release_workflow_script.main(
        [
            "runtime-readiness",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [tmp_path.resolve()]


def test_release_workflow_main_dispatches_verify_tag_preflight(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch release tag preflight checks."""
    from tests.scripts import release_workflow as release_workflow_script

    seen_calls: list[tuple[str, Path, str | None]] = []

    def fake_verify_release_tag_preflight(
        *,
        tag: str,
        repo_root: Path,
        repository: str | None,
    ) -> None:
        seen_calls.append((tag, repo_root, repository))

    test_context.patch.patch_object(
        release_workflow_script,
        "verify_release_tag_preflight",
        new=fake_verify_release_tag_preflight,
    )

    exit_code = release_workflow_script.main(
        [
            "verify-tag-preflight",
            "--tag",
            "v0.1.0",
            "--repo-root",
            str(tmp_path),
            "--repository",
            "jsugg/strongclaw",
        ]
    )

    assert exit_code == 0
    assert seen_calls == [("v0.1.0", tmp_path.resolve(), "jsugg/strongclaw")]


def test_release_workflow_main_dispatches_write_release_metadata(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch release metadata generation."""
    from tests.scripts import release_workflow as release_workflow_script

    seen_calls: list[tuple[str, Path, Path, Path]] = []

    def fake_write_release_metadata(
        *,
        tag: str,
        repo_root: Path,
        dist_dir: Path,
        sbom_path: Path,
    ) -> tuple[Path, Path]:
        seen_calls.append((tag, repo_root, dist_dir, sbom_path))
        return dist_dir / release_helpers.RELEASE_MANIFEST_NAME, dist_dir / "SHA256SUMS"

    test_context.patch.patch_object(
        release_workflow_script,
        "write_release_metadata",
        new=fake_write_release_metadata,
    )

    exit_code = release_workflow_script.main(
        [
            "write-release-metadata",
            "--tag",
            "v0.1.0",
            "--repo-root",
            str(tmp_path / "repo"),
            "--dist-dir",
            str(tmp_path / "dist"),
            "--sbom-path",
            str(tmp_path / "sbom.spdx.json"),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [
        (
            "v0.1.0",
            (tmp_path / "repo").resolve(),
            (tmp_path / "dist").resolve(),
            (tmp_path / "sbom.spdx.json").resolve(),
        )
    ]


def test_verify_tag_version_parity_rejects_mismatched_tag(tmp_path: Path) -> None:
    """Parity checks should fail when the release tag mismatches package metadata."""
    (tmp_path / "src" / "clawops").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "clawops" / "__init__.py").write_text(
        '__version__ = "0.1.0"\n',
        encoding="utf-8",
    )

    with pytest.raises(ci_workflows.CiWorkflowError, match="release tag/version mismatch"):
        ci_workflows.verify_tag_version_parity(tag="v0.1.1", repo_root=tmp_path)


def test_run_release_runtime_readiness_executes_expected_commands(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Runtime readiness should execute the checklist commands in sequence."""
    seen_commands: list[list[str]] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del env, timeout_seconds, capture_output
        if command[:3] != [sys.executable, "-m", "openclaw"]:
            assert cwd == tmp_path
        seen_commands.append(command)
        if command[:3] == [sys.executable, "-m", "openclaw"]:
            return SimpleNamespace(stdout="", stderr="")
        return None

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(release_helpers.shutil, "which", return_value=None)

    ci_workflows.run_release_runtime_readiness(repo_root=tmp_path)

    assert seen_commands[0][:4] == [sys.executable, "-m", "clawops", "doctor"]
    assert any(command[:3] == [sys.executable, "-m", "openclaw"] for command in seen_commands)
    assert any(
        command[:5]
        == [
            sys.executable,
            "./tests/scripts/security_workflow.py",
            "run-channels-runtime-smoke",
            "--repo-root",
            ".",
        ]
        for command in seen_commands
    )
    assert any(
        command[:4]
        == [
            sys.executable,
            "./tests/scripts/launch_readiness.py",
            "generate-audit-packet",
            "--output-dir",
        ]
        for command in seen_commands
    )
    assert any(
        command[:4]
        == [
            "uv",
            "run",
            "pytest",
            "-q",
        ]
        and command[-1]
        == "tests/suites/contracts/repo/launch_readiness/test_launch_readiness_audit_packet.py"
        for command in seen_commands
    )


def test_run_release_runtime_readiness_runs_launch_contract_in_live_mode(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Runtime readiness should execute launch-readiness contracts with live artifact env wiring."""
    seen_calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del cwd, timeout_seconds, capture_output
        seen_calls.append((command, env))
        if command[:3] == [sys.executable, "-m", "openclaw"]:
            return SimpleNamespace(stdout="", stderr="")
        return None

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)
    test_context.patch.patch_object(release_helpers.shutil, "which", return_value=None)

    ci_workflows.run_release_runtime_readiness(repo_root=tmp_path)

    live_call = next(
        (call for call in seen_calls if call[0][:4] == ["uv", "run", "pytest", "-q"]),
        None,
    )
    assert live_call is not None
    command, env = live_call
    assert command[-1] == release_helpers.LAUNCH_READINESS_CONTRACT_TEST_PATH
    assert env is not None
    assert env["STRONGCLAW_LAUNCH_READINESS_ARTIFACT_MODE"] == "live"
    assert env["STRONGCLAW_LAUNCH_READINESS_ARTIFACT_ROOT"]


def test_release_policy_rejects_forbidden_path_in_wheel(tmp_path: Path) -> None:
    """Artifact policy should fail when a forbidden runtime-state path ships in a wheel."""
    wheel_path = tmp_path / "clawops-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, mode="w") as archive:
        archive.writestr(
            "clawops/assets/platform/compose/state/neo4j/data.db",
            "bad",
        )

    with pytest.raises(ci_workflows.CiWorkflowError, match="contains forbidden path"):
        release_helpers.enforce_artifact_content_policy(wheel_path)


def test_release_policy_rejects_forbidden_path_in_sdist(tmp_path: Path) -> None:
    """Artifact policy should fail when a forbidden runtime-state path ships in an sdist."""
    sdist_path = tmp_path / "clawops-0.1.0.tar.gz"
    payload_path = tmp_path / "state.db"
    payload_path.write_text("bad", encoding="utf-8")
    with tarfile.open(sdist_path, mode="w:gz") as archive:
        archive.add(
            payload_path,
            arcname="clawops-0.1.0/clawops/assets/platform/compose/state/state.db",
        )

    with pytest.raises(ci_workflows.CiWorkflowError, match="contains forbidden path"):
        release_helpers.enforce_artifact_content_policy(sdist_path)


def test_required_runtime_asset_paths_exist_in_packaged_assets_tree() -> None:
    """Runtime-asset smoke requirements should only reference shipped packaged files."""
    packaged_asset_root = REPO_ROOT / "src" / "clawops" / "assets" / "platform"
    missing_paths = [
        relative_path
        for relative_path in release_helpers.REQUIRED_RUNTIME_ASSET_PATHS
        if not (packaged_asset_root / relative_path).is_file()
    ]

    assert missing_paths == []
