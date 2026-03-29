"""Helpers for the compatibility-matrix workflow."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from clawops.common import write_json
from clawops.openclaw_config import PROFILES, render_openclaw_profile
from clawops.strongclaw_bootstrap import ensure_varlock_installed, install_lossless_claw_asset
from tests.utils.helpers._ci_workflows.common import (
    CiWorkflowError,
    patched_environment,
    write_github_env,
)


@dataclass(frozen=True, slots=True)
class SetupSmokePaths:
    """Resolved filesystem layout for the setup-smoke lane."""

    tmp_root: Path
    home_dir: Path
    config_dir: Path
    data_dir: Path
    state_dir: Path


def resolve_setup_smoke_paths(runner_temp: Path) -> SetupSmokePaths:
    """Resolve the compatibility-matrix working paths."""
    resolved_runner_temp = runner_temp.expanduser().resolve()
    tmp_root = (
        Path(
            os.environ.get(
                "SETUP_COMPAT_ROOT", str(resolved_runner_temp / "strongclaw-setup-compat")
            )
        )
        .expanduser()
        .resolve()
    )
    return SetupSmokePaths(
        tmp_root=tmp_root,
        home_dir=tmp_root / "home",
        config_dir=Path(os.environ.get("STRONGCLAW_CONFIG_DIR", str(tmp_root / "config")))
        .expanduser()
        .resolve(),
        data_dir=Path(os.environ.get("STRONGCLAW_DATA_DIR", str(tmp_root / "data")))
        .expanduser()
        .resolve(),
        state_dir=Path(os.environ.get("STRONGCLAW_STATE_DIR", str(tmp_root / "state")))
        .expanduser()
        .resolve(),
    )


def prepare_setup_smoke(
    repo_root: Path,
    runner_temp: Path,
    *,
    github_env_file: Path | None = None,
) -> SetupSmokePaths:
    """Create the compatibility-matrix setup-smoke environment."""
    resolved_repo_root = repo_root.expanduser().resolve()
    paths = resolve_setup_smoke_paths(runner_temp)
    for directory in (
        paths.tmp_root,
        paths.home_dir,
        paths.config_dir,
        paths.data_dir,
        paths.state_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    exported_env = {
        "HOME": str(paths.home_dir),
        "SETUP_COMPAT_ROOT": str(paths.tmp_root),
        "STRONGCLAW_CONFIG_DIR": str(paths.config_dir),
        "STRONGCLAW_DATA_DIR": str(paths.data_dir),
        "STRONGCLAW_STATE_DIR": str(paths.state_dir),
    }
    with patched_environment(exported_env):
        ensure_varlock_installed()
        install_lossless_claw_asset(resolved_repo_root, home_dir=paths.home_dir)
        rendered = render_openclaw_profile(
            profile_name="hypermemory",
            repo_root=resolved_repo_root,
            home_dir=paths.home_dir,
        )
        write_json(paths.tmp_root / "openclaw.json", rendered)
    write_github_env(exported_env, github_env_file)
    return paths


def assert_lossless_claw_installed(tmp_root: Path) -> None:
    """Assert that the lossless-claw plugin manifest exists."""
    resolved_tmp_root = tmp_root.expanduser().resolve()
    manifest_path = (
        resolved_tmp_root / "data" / "plugins" / "lossless-claw" / "openclaw.plugin.json"
    )
    if not manifest_path.is_file():
        raise CiWorkflowError(f"missing lossless-claw plugin manifest at {manifest_path}")


def assert_hypermemory_config(tmp_root: Path) -> None:
    """Assert that the rendered config points at the managed hypermemory file."""
    resolved_tmp_root = tmp_root.expanduser().resolve()
    payload_path = resolved_tmp_root / "openclaw.json"
    if not payload_path.is_file():
        raise CiWorkflowError(f"missing rendered OpenClaw config at {payload_path}")
    payload_object: object = json.loads(payload_path.read_text(encoding="utf-8"))
    payload = _require_str_object_dict(payload_object, label="OpenClaw config")
    plugins = _require_str_object_dict(payload.get("plugins"), label="plugins block")
    entries = _require_str_object_dict(plugins.get("entries"), label="entries block")
    hypermemory_entry = _require_str_object_dict(
        entries.get("strongclaw-hypermemory"),
        label="strongclaw-hypermemory entry",
    )
    plugin_config = _require_str_object_dict(
        hypermemory_entry.get("config"),
        label="strongclaw-hypermemory config",
    )

    expected_config_path = resolved_tmp_root / "config" / "memory" / "hypermemory.yaml"
    actual_path: object = plugin_config.get("configPath")
    auto_recall: object = plugin_config.get("autoRecall")
    if not isinstance(actual_path, str):
        raise CiWorkflowError("strongclaw-hypermemory configPath must be a string")
    if actual_path != expected_config_path.as_posix():
        raise CiWorkflowError(
            f"unexpected hypermemory config path: expected {expected_config_path.as_posix()}, got {actual_path!r}"
        )
    if auto_recall is not True:
        raise CiWorkflowError(f"unexpected autoRecall setting: expected True, got {auto_recall!r}")


def assert_openclaw_profiles_render(
    repo_root: Path,
    runner_temp: Path,
) -> list[str]:
    """Render every OpenClaw profile and persist artifacts for nightly inspection."""
    resolved_repo_root = repo_root.expanduser().resolve()
    resolved_runner_temp = runner_temp.expanduser().resolve()
    nightly_root = resolved_runner_temp / "strongclaw" / "nightly"
    home_dir = nightly_root / "profile-home"
    runtime_root = nightly_root / "profile-runtime-root"
    output_dir = nightly_root / "openclaw-profiles"
    for directory in (home_dir, runtime_root, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    rendered_profiles: list[str] = []
    with patched_environment(
        {
            "HOME": str(home_dir),
            "STRONGCLAW_RUNTIME_ROOT": str(runtime_root),
        }
    ):
        for profile_name in sorted(PROFILES):
            rendered = render_openclaw_profile(
                profile_name=profile_name,
                repo_root=resolved_repo_root,
                home_dir=home_dir,
            )
            write_json(output_dir / f"{profile_name}.json", rendered)
            rendered_profiles.append(profile_name)
    return rendered_profiles


def _require_str_object_dict(value: object, *, label: str) -> dict[str, object]:
    """Validate that *value* is a string-keyed mapping."""
    if not isinstance(value, dict):
        raise CiWorkflowError(f"{label} must be a mapping")
    validated: dict[str, object] = {}
    raw_value = cast(dict[object, object], value)
    for key, entry in raw_value.items():
        if not isinstance(key, str):
            raise CiWorkflowError(f"{label} must use string keys")
        validated[key] = entry
    return validated
