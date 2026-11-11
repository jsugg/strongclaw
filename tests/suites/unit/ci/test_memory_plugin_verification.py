"""Unit coverage for memory-plugin verification helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from clawops.strongclaw_node_tools import (
    LOCK_FILE_NAME,
    MANIFEST_FILE_NAME,
    NODE_TOOLS_ASSET_DIR,
    LocalPrefixWriter,
    NodeToolsBundle,
    NodeToolsError,
    PrefixWriter,
    load_node_tools_bundle,
)
from tests.plugins.infrastructure.context import TestContext
from tests.scripts import memory_plugin_verification as memory_plugin_script
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import memory_plugin as memory_plugin_helpers
from tests.utils.helpers._ci_workflows.common import CiWorkflowError
from tests.utils.helpers.repo import REPO_ROOT


def _seed_node_tools_bundle(repo_root: Path) -> None:
    """Copy the committed node-tools manifest and lock into a scratch repo root."""
    target_dir = repo_root / NODE_TOOLS_ASSET_DIR
    target_dir.mkdir(parents=True)
    for file_name in (MANIFEST_FILE_NAME, LOCK_FILE_NAME):
        shutil.copyfile(REPO_ROOT / NODE_TOOLS_ASSET_DIR / file_name, target_dir / file_name)


def test_run_vendored_host_checks_installs_locked_cli_and_clears_aws_env(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Host checks install the committed lock, prefix PATH, and clear ambient AWS variables."""
    repo_root = tmp_path / "repo"
    plugin_dir = repo_root / "platform" / "plugins" / "memory-lancedb-pro"
    plugin_dir.mkdir(parents=True)
    _seed_node_tools_bundle(repo_root)
    staged: list[tuple[NodeToolsBundle, Path, PrefixWriter]] = []
    smoked: list[tuple[Path, NodeToolsBundle]] = []
    seen_calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []

    def fake_stage(bundle: NodeToolsBundle, target_dir: Path, writer: PrefixWriter) -> None:
        assert target_dir.is_dir()
        staged.append((bundle, target_dir, writer))

    def fake_smoke(bin_dir: Path, bundle: NodeToolsBundle) -> None:
        smoked.append((bin_dir, bundle))

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del timeout_seconds, capture_output
        seen_calls.append((command, cwd, env))
        return None

    test_context.patch.patch_object(memory_plugin_helpers, "stage_node_tools_tree", new=fake_stage)
    test_context.patch.patch_object(memory_plugin_helpers, "smoke_test_node_tools", new=fake_smoke)
    test_context.patch.patch_object(memory_plugin_helpers, "run_checked", new=fake_run_checked)
    test_context.env.set("PATH", "/usr/bin")
    test_context.env.set("AWS_PROFILE", "default")
    test_context.env.set("AWS_REGION", "us-east-1")

    ci_workflows.run_vendored_host_checks(repo_root)

    committed = load_node_tools_bundle(REPO_ROOT / NODE_TOOLS_ASSET_DIR)
    [(bundle, tool_dir, writer)] = staged
    assert bundle.digest == committed.digest
    assert bundle.manifest_path == (repo_root / NODE_TOOLS_ASSET_DIR / MANIFEST_FILE_NAME).resolve()
    assert isinstance(writer, LocalPrefixWriter)
    bin_dir = tool_dir / "node_modules" / ".bin"
    assert smoked == [(bin_dir, bundle)]
    assert not tool_dir.exists()

    assert [(command, cwd) for command, cwd, _ in seen_calls] == [
        (["npm", "ci", "--no-fund", "--no-audit"], plugin_dir.resolve()),
        (["npm", "run", "test:openclaw-host"], plugin_dir.resolve()),
    ]
    for _, _, env in seen_calls:
        assert env is not None
        assert "AWS_PROFILE" not in env
        assert "AWS_REGION" not in env
        assert env["PATH"] == f"{bin_dir}:/usr/bin"


def test_run_vendored_host_checks_reports_locked_install_failure(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """A failed locked install surfaces as a CI error and skips the plugin checks."""
    repo_root = tmp_path / "repo"
    _seed_node_tools_bundle(repo_root)
    tool_dirs: list[Path] = []
    seen_commands: list[list[str]] = []

    def failing_stage(bundle: NodeToolsBundle, target_dir: Path, writer: PrefixWriter) -> None:
        del bundle, writer
        tool_dirs.append(target_dir)
        raise NodeToolsError("npm ci failed in /tmp (ETARGET)")

    def fake_run_checked(command: list[str], **kwargs: Any) -> Any:
        del kwargs
        seen_commands.append(command)
        return None

    test_context.patch.patch_object(
        memory_plugin_helpers, "stage_node_tools_tree", new=failing_stage
    )
    test_context.patch.patch_object(memory_plugin_helpers, "run_checked", new=fake_run_checked)

    with pytest.raises(CiWorkflowError, match=r"locked node-tools install failed: .*ETARGET"):
        ci_workflows.run_vendored_host_checks(repo_root)

    assert seen_commands == []
    assert [tool_dir.exists() for tool_dir in tool_dirs] == [False]


def test_main_dispatches_run_vendored_host_checks(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI dispatches host checks with only the repo root; no package spec is accepted."""
    seen_roots: list[Path] = []
    test_context.patch.patch_object(
        memory_plugin_script, "run_vendored_host_checks", new=seen_roots.append
    )

    exit_code = memory_plugin_script.main(
        ["run-vendored-host-checks", "--repo-root", str(tmp_path / "repo")]
    )

    assert exit_code == 0
    assert seen_roots == [(tmp_path / "repo").resolve()]
    with pytest.raises(SystemExit):
        memory_plugin_script.main(
            ["run-vendored-host-checks", "--package-spec", "openclaw@2026.3.13"]
        )


def test_wait_for_qdrant_retries_until_ready(test_context: TestContext) -> None:
    """Qdrant readiness should retry transient probe failures."""
    attempts: list[str] = []
    sleeps: list[float] = []

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb
            return None

    def fake_urlopen(url: str, timeout: int) -> _Response:
        attempts.append(url)
        assert timeout == 5
        if len(attempts) < 3:
            raise OSError("not ready")
        return _Response()

    test_context.patch.patch_object(
        memory_plugin_helpers.urllib.request,
        "urlopen",
        new=fake_urlopen,
    )
    test_context.patch.patch_object(memory_plugin_helpers.time, "sleep", new=sleeps.append)

    ci_workflows.wait_for_qdrant("http://127.0.0.1:6333/healthz", attempts=4, sleep_seconds=1.5)

    assert attempts == ["http://127.0.0.1:6333/healthz"] * 3
    assert sleeps == [1.5, 1.5]


def test_run_clawops_memory_migration_invokes_dry_run_cli(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Memory migration checks should execute the dry-run clawops migration CLI."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runner_temp = tmp_path / "runner-temp"
    seen_calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del timeout_seconds, capture_output
        seen_calls.append((command, cwd, env))
        return None

    test_context.patch.patch_object(memory_plugin_helpers, "run_checked", new=fake_run_checked)
    test_context.env.remove("PYTHONPATH")

    report_path = ci_workflows.run_clawops_memory_migration(repo_root, runner_temp=runner_temp)

    assert report_path == runner_temp.resolve() / "clawops-memory-migration-report.json"
    command, command_cwd, command_env = seen_calls[0]
    assert command[:8] == [
        "uv",
        "run",
        "python",
        "-m",
        "clawops",
        "memory",
        "migrate-hypermemory-to-pro",
        "--dry-run",
    ]
    assert command[-2:] == ["--report", str(report_path)]
    assert command_cwd == repo_root.resolve()
    assert command_env is not None
    assert command_env["PYTHONPATH"] == "src"


def test_main_dispatches_wait_for_qdrant(test_context: TestContext) -> None:
    """The CLI should dispatch Qdrant readiness checks."""
    seen_calls: list[tuple[str, int, float]] = []

    def fake_wait_for_qdrant(url: str, *, attempts: int = 30, sleep_seconds: float = 2.0) -> None:
        seen_calls.append((url, attempts, sleep_seconds))

    test_context.patch.patch_object(
        memory_plugin_script,
        "wait_for_qdrant",
        new=fake_wait_for_qdrant,
    )

    exit_code = memory_plugin_script.main(
        ["wait-for-qdrant", "--url", "http://127.0.0.1:6333/healthz", "--attempts", "12"]
    )

    assert exit_code == 0
    assert seen_calls == [("http://127.0.0.1:6333/healthz", 12, 2.0)]


def test_main_dispatches_run_clawops_memory_migration(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch dry-run clawops memory migration checks."""
    seen_calls: list[tuple[Path, Path | None]] = []

    def fake_run_clawops_memory_migration(
        repo_root: Path,
        *,
        runner_temp: Path | None = None,
    ) -> Path:
        seen_calls.append((repo_root, runner_temp))
        return tmp_path / "report.json"

    test_context.patch.patch_object(
        memory_plugin_script,
        "run_clawops_memory_migration",
        new=fake_run_clawops_memory_migration,
    )

    exit_code = memory_plugin_script.main(
        [
            "run-clawops-memory-migration",
            "--repo-root",
            str(tmp_path / "repo"),
            "--runner-temp",
            str(tmp_path / "runner-temp"),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [((tmp_path / "repo").resolve(), (tmp_path / "runner-temp").resolve())]
