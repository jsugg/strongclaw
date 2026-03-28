"""Integration coverage for installed-package runtime assets."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import zipfile

from tests.utils.helpers.repo import REPO_ROOT


def _run_checked(
    command: list[str], *, cwd: pathlib.Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess and require success."""
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_installed_package_can_render_openclaw_config_outside_source_checkout(
    tmp_path: pathlib.Path,
) -> None:
    dist_dir = tmp_path / "dist"
    site_packages = tmp_path / "site-packages"
    workspace = tmp_path / "workspace"
    home_dir = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg-config"
    xdg_data_home = tmp_path / "xdg-data"
    output_path = tmp_path / "openclaw.json"

    dist_dir.mkdir()
    site_packages.mkdir()
    workspace.mkdir()
    home_dir.mkdir()
    xdg_config_home.mkdir()
    xdg_data_home.mkdir()

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONPATH"] = str(site_packages)
    env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    env["XDG_DATA_HOME"] = str(xdg_data_home)

    _run_checked(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=REPO_ROOT,
        env=env,
    )
    wheel_path = next(dist_dir.glob("clawops-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel_archive:
        wheel_archive.extractall(site_packages)

    result = _run_checked(
        [
            sys.executable,
            "-m",
            "clawops",
            "render-openclaw-config",
            "--profile",
            "hypermemory",
            "--home-dir",
            str(home_dir),
            "--output",
            str(output_path),
        ],
        cwd=workspace,
        env=env,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    admin = next(agent for agent in payload["agents"]["list"] if agent["id"] == "admin")
    hypermemory_config = payload["plugins"]["entries"]["strongclaw-hypermemory"]["config"]
    plugin_paths = payload["plugins"]["load"]["paths"]
    generated_memory_config = xdg_config_home / "strongclaw" / "memory" / "hypermemory.yaml"

    assert "Rendered" in result.stdout
    assert admin["workspace"] == str(xdg_data_home / "strongclaw" / "workspace" / "admin")
    assert hypermemory_config["configPath"] == str(generated_memory_config)
    assert generated_memory_config.exists()
    assert any(
        path.endswith("clawops/assets/platform/plugins/strongclaw-hypermemory")
        for path in plugin_paths
    )
