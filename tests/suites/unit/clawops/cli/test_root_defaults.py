"""Unit tests for inferred CLI repo and project roots."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import pathlib

import pytest

import clawops.acp_runner as acp_runner
import clawops.openclaw_config as openclaw_config
import clawops.strongclaw_baseline as strongclaw_baseline
from clawops.root_detection import resolve_project_root, resolve_strongclaw_repo_root
from clawops.runtime_assets import ASSET_ROOT_ENV_VAR, PACKAGED_ASSET_ROOT
from tests.plugins.infrastructure.context import TestContext


def _init_strongclaw_repo(repo_root: pathlib.Path) -> pathlib.Path:
    """Create the minimal StrongClaw marker set for root discovery tests."""
    repo_root.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        "[project]\nname = 'strongclaw-test'\n", encoding="utf-8"
    )
    (repo_root / "platform").mkdir(parents=True)
    (repo_root / "src" / "clawops").mkdir(parents=True)
    return repo_root


def test_resolve_strongclaw_repo_root_discovers_matching_ancestor(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = _init_strongclaw_repo(tmp_path / "repo")
    nested = repo_root / "platform" / "docs" / "nested"
    nested.mkdir(parents=True)

    resolved = resolve_strongclaw_repo_root(cwd=nested, fallback=None)

    assert resolved == repo_root.resolve()


def test_resolve_project_root_prefers_the_nearest_git_ancestor(tmp_path: pathlib.Path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".git").mkdir(parents=True)
    nested = project_root / "src" / "feature"
    nested.mkdir(parents=True)

    resolved = resolve_project_root(cwd=nested)

    assert resolved == project_root.resolve()


def test_render_openclaw_config_main_uses_packaged_assets_from_source_checkout_by_default(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    repo_root = _init_strongclaw_repo(tmp_path / "repo")
    nested = repo_root / "platform" / "docs"
    nested.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "openclaw.json"
    captured_repo_root: pathlib.Path | None = None

    def _render_openclaw_profile(
        *,
        profile_name: str,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path | None,
        user_timezone: str | None = None,
        extra_overlays: tuple[pathlib.Path, ...] = (),
    ) -> dict[str, object]:
        del profile_name, home_dir, user_timezone, extra_overlays
        nonlocal captured_repo_root
        captured_repo_root = repo_root
        return {"ok": True}

    def _materialize_runtime_memory_configs(
        *,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path,
        user_timezone: str | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        del home_dir, user_timezone
        return repo_root / "managed-memory.yaml", repo_root / "managed-memory.sqlite.yaml"

    test_context.chdir(nested)
    test_context.patch.patch_object(
        openclaw_config,
        "render_openclaw_profile",
        new=_render_openclaw_profile,
    )
    test_context.patch.patch_object(
        openclaw_config,
        "materialize_runtime_memory_configs",
        new=_materialize_runtime_memory_configs,
    )

    exit_code = openclaw_config.main(["--profile", "hypermemory", "--output", str(output_path)])

    assert exit_code == 0
    assert captured_repo_root == PACKAGED_ASSET_ROOT
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"ok": True}
    assert "Rendered" in capsys.readouterr().out


def test_render_openclaw_config_main_honors_env_asset_root_override(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    repo_root = _init_strongclaw_repo(tmp_path / "repo")
    nested = repo_root / "platform" / "docs"
    nested.mkdir(parents=True, exist_ok=True)
    output_path = tmp_path / "openclaw.json"
    captured_repo_root: pathlib.Path | None = None

    def _render_openclaw_profile(
        *,
        profile_name: str,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path | None,
        user_timezone: str | None = None,
        extra_overlays: tuple[pathlib.Path, ...] = (),
    ) -> dict[str, object]:
        del profile_name, home_dir, user_timezone, extra_overlays
        nonlocal captured_repo_root
        captured_repo_root = repo_root
        return {"ok": True}

    def _materialize_runtime_memory_configs(
        *,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path,
        user_timezone: str | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        del home_dir, user_timezone
        return repo_root / "managed-memory.yaml", repo_root / "managed-memory.sqlite.yaml"

    test_context.chdir(nested)
    test_context.env.set(ASSET_ROOT_ENV_VAR, str(repo_root))
    test_context.patch.patch_object(
        openclaw_config,
        "render_openclaw_profile",
        new=_render_openclaw_profile,
    )
    test_context.patch.patch_object(
        openclaw_config,
        "materialize_runtime_memory_configs",
        new=_materialize_runtime_memory_configs,
    )

    exit_code = openclaw_config.main(["--profile", "hypermemory", "--output", str(output_path)])

    assert exit_code == 0
    assert captured_repo_root == repo_root.resolve()
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"ok": True}
    assert "Rendered" in capsys.readouterr().out


def test_strongclaw_baseline_infers_repo_root_and_runs_dir_from_cwd(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    repo_root = _init_strongclaw_repo(tmp_path / "repo")
    nested = repo_root / "src" / "clawops"
    nested.mkdir(parents=True, exist_ok=True)
    recorded_repo_root: pathlib.Path | None = None
    recorded_runs_dir: pathlib.Path | None = None
    recorded_degraded: bool | None = None

    def _verify_baseline(
        repo_root_arg: pathlib.Path,
        *,
        runs_dir: pathlib.Path,
        degraded: bool = False,
        include_browser_lab: bool = False,
        env_mode: str = "managed",
    ) -> dict[str, object]:
        nonlocal recorded_repo_root, recorded_runs_dir, recorded_degraded
        assert include_browser_lab is False
        assert env_mode == "managed"
        recorded_repo_root = repo_root_arg
        recorded_runs_dir = runs_dir
        recorded_degraded = degraded
        return {"ok": True, "runsDir": str(runs_dir), "degraded": degraded}

    test_context.chdir(nested)
    test_context.patch.patch_object(strongclaw_baseline, "verify_baseline", new=_verify_baseline)

    exit_code = strongclaw_baseline.main(["verify"])

    assert exit_code == 0
    assert recorded_repo_root == repo_root.resolve()
    assert recorded_runs_dir == repo_root.resolve() / ".tmp" / "harness"
    assert recorded_degraded is False
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_acp_runner_infers_project_root_without_explicit_flag(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    project_root = tmp_path / "project"
    (project_root / ".git").mkdir(parents=True)
    nested = project_root / "src" / "feature"
    nested.mkdir(parents=True)
    workspace = project_root / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"

    test_context.chdir(nested)

    args = acp_runner.parse_args(
        [
            "--backend",
            "codex",
            "--prompt",
            "Summarize the worktree",
            "--workspace",
            str(workspace),
            "--state-dir",
            str(state_dir),
        ]
    )
    spec = acp_runner._resolve_session_spec(args)

    assert spec.project.root == project_root.resolve()
