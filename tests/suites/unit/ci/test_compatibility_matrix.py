"""Unit coverage for compatibility-matrix CI helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.scripts import compatibility_matrix as compatibility_matrix_script
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import compatibility as compatibility_helpers


def test_prepare_setup_smoke_writes_context_and_exports_env(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Setup-smoke preparation should render config and export the managed paths."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runner_temp = tmp_path / "runner-temp"
    github_env_file = tmp_path / "github.env"
    observed_home: list[str] = []
    observed_install_home: list[Path | None] = []

    def fake_ensure_varlock_installed() -> None:
        observed_home.append(os.environ["HOME"])

    def fake_install_lossless_claw_asset(
        resolved_repo_root: Path,
        *,
        home_dir: Path | None = None,
    ) -> None:
        assert resolved_repo_root == repo_root.resolve()
        observed_install_home.append(home_dir)
        manifest_path = (
            Path(os.environ["STRONGCLAW_DATA_DIR"])
            / "plugins"
            / "lossless-claw"
            / "openclaw.plugin.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{}", encoding="utf-8")

    def fake_render_openclaw_profile(
        *,
        profile_name: str,
        repo_root: Path,
        home_dir: Path,
    ) -> dict[str, object]:
        assert profile_name == "hypermemory"
        assert repo_root == (tmp_path / "repo").resolve()
        assert home_dir.name == "home"
        return {
            "plugins": {
                "entries": {
                    "strongclaw-hypermemory": {
                        "config": {
                            "configPath": (
                                Path(os.environ["STRONGCLAW_CONFIG_DIR"])
                                / "memory"
                                / "hypermemory.yaml"
                            ).as_posix(),
                            "autoRecall": True,
                        }
                    }
                }
            }
        }

    test_context.patch.patch_object(
        compatibility_helpers,
        "ensure_varlock_installed",
        new=fake_ensure_varlock_installed,
    )
    test_context.patch.patch_object(
        compatibility_helpers,
        "install_lossless_claw_asset",
        new=fake_install_lossless_claw_asset,
    )
    test_context.patch.patch_object(
        compatibility_helpers,
        "render_openclaw_profile",
        new=fake_render_openclaw_profile,
    )

    paths = ci_workflows.prepare_setup_smoke(
        repo_root,
        runner_temp,
        github_env_file=github_env_file,
    )

    assert observed_home == [str(paths.home_dir)]
    assert observed_install_home == [paths.home_dir]
    exported = github_env_file.read_text(encoding="utf-8")
    assert f"HOME={paths.home_dir}" in exported
    assert f"SETUP_COMPAT_ROOT={paths.tmp_root}" in exported
    payload = json.loads((paths.tmp_root / "openclaw.json").read_text(encoding="utf-8"))
    assert (
        payload["plugins"]["entries"]["strongclaw-hypermemory"]["config"]["configPath"]
        == (paths.config_dir / "memory" / "hypermemory.yaml").as_posix()
    )


def test_assert_lossless_claw_installed_rejects_missing_manifest(tmp_path: Path) -> None:
    """Missing plugin manifests should fail fast."""
    with pytest.raises(ci_workflows.CiWorkflowError, match="missing lossless-claw plugin manifest"):
        ci_workflows.assert_lossless_claw_installed(tmp_path)


def test_assert_hypermemory_config_requires_expected_path(tmp_path: Path) -> None:
    """The rendered config should keep the managed hypermemory contract."""
    payload_path = tmp_path / "openclaw.json"
    payload_path.write_text(
        json.dumps(
            {
                "plugins": {
                    "entries": {
                        "strongclaw-hypermemory": {
                            "config": {
                                "configPath": "/tmp/wrong.yaml",
                                "autoRecall": True,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ci_workflows.CiWorkflowError, match="unexpected hypermemory config path"):
        ci_workflows.assert_hypermemory_config(tmp_path)


def test_main_dispatches_prepare_setup_smoke(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """The CLI should dispatch setup-smoke preparation."""
    seen_calls: list[tuple[Path, Path, Path | None]] = []

    def fake_prepare_setup_smoke(
        repo_root: Path,
        runner_temp: Path,
        *,
        github_env_file: Path | None = None,
    ) -> None:
        seen_calls.append((repo_root, runner_temp, github_env_file))

    test_context.patch.patch_object(
        compatibility_matrix_script,
        "prepare_setup_smoke",
        new=fake_prepare_setup_smoke,
    )
    github_env_file = tmp_path / "github.env"
    exit_code = compatibility_matrix_script.main(
        [
            "prepare-setup-smoke",
            "--repo-root",
            str(tmp_path / "repo"),
            "--runner-temp",
            str(tmp_path / "runner-temp"),
            "--github-env-file",
            str(github_env_file),
        ]
    )

    assert exit_code == 0
    assert seen_calls == [
        (
            (tmp_path / "repo").resolve(),
            (tmp_path / "runner-temp").resolve(),
            github_env_file.resolve(),
        )
    ]
