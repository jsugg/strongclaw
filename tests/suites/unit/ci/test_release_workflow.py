"""Unit coverage for release workflow helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import release as release_helpers


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

    test_context.patch.patch_object(release_helpers, "run_checked", new=fake_run_checked)

    ci_workflows.verify_release_artifacts(dist_dir)

    assert seen_commands[0][:3] == ["uv", "run", "twine"]
    assert any(command[-1] == str(wheel_path) for command in seen_commands if "install" in command)
    assert any(command[-1] == str(sdist_path) for command in seen_commands if "install" in command)
    assert any(
        command[-1].startswith("import importlib.metadata as metadata;")
        for command in seen_commands
    )


def test_publish_github_release_creates_when_missing(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Release publishing should create the release when it does not exist yet."""
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "artifact.whl").write_text("wheel", encoding="utf-8")
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
