"""Runtime asset and managed-layout resolution for StrongClaw."""

from __future__ import annotations

import dataclasses
import os
import pathlib
import shutil
from collections.abc import Mapping
from typing import Final

from clawops.app_paths import (
    strongclaw_config_dir,
    strongclaw_data_dir,
    strongclaw_log_dir,
    strongclaw_memory_config_dir,
    strongclaw_plugin_dir,
    strongclaw_plugins_dir,
    strongclaw_runtime_root,
    strongclaw_state_dir,
    strongclaw_upstream_repo_dir,
    strongclaw_varlock_dir,
    strongclaw_workspace_dir,
    strongclaw_worktrees_dir,
)
from clawops.root_detection import STRONGCLAW_REPO_MARKERS

PACKAGED_ASSET_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent / "assets"
ASSET_ROOT_ENV_VAR: Final[str] = "STRONGCLAW_ASSET_ROOT"
PLATFORM_DIR_NAME: Final[str] = "platform"
MEMORY_CONFIG_RELATIVE_DIR: Final[pathlib.Path] = pathlib.Path("platform/configs/memory")
VARLOCK_CONFIG_RELATIVE_DIR: Final[pathlib.Path] = pathlib.Path("platform/configs/varlock")
DEFAULT_DEV_OPENCLAW_PROFILE: Final[str] = "strongclaw-dev"


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeLayout:
    """Resolved StrongClaw runtime roots."""

    asset_root: pathlib.Path
    platform_root: pathlib.Path
    source_checkout_root: pathlib.Path | None
    real_home_dir: pathlib.Path
    runtime_root: pathlib.Path | None
    uses_isolated_runtime: bool
    strongclaw_root: pathlib.Path
    strongclaw_data_root: pathlib.Path
    strongclaw_config_root: pathlib.Path
    strongclaw_state_root: pathlib.Path
    strongclaw_log_root: pathlib.Path
    workspace_root: pathlib.Path
    upstream_repo_root: pathlib.Path
    worktrees_root: pathlib.Path
    plugin_root: pathlib.Path
    varlock_env_root: pathlib.Path
    memory_config_root: pathlib.Path
    hypermemory_config_path: pathlib.Path
    hypermemory_sqlite_config_path: pathlib.Path
    openclaw_home: pathlib.Path
    openclaw_state_dir: pathlib.Path
    openclaw_config_path: pathlib.Path
    openclaw_profile: str | None

    @property
    def uses_packaged_assets(self) -> bool:
        """Return whether runtime assets come from the installed package bundle."""
        return self.asset_root == PACKAGED_ASSET_ROOT

    @property
    def home_dir(self) -> pathlib.Path:
        """Backward-compatible alias for the real user home directory."""
        return self.real_home_dir


def _resolve_path(value: pathlib.Path | str) -> pathlib.Path:
    """Return one expanded absolute path."""
    return pathlib.Path(value).expanduser().resolve()


def _require_platform_root(root: pathlib.Path) -> pathlib.Path:
    """Require that *root* contains the packaged/source platform tree."""
    if not (root / PLATFORM_DIR_NAME).is_dir():
        raise FileNotFoundError(
            f"StrongClaw asset root must contain {PLATFORM_DIR_NAME}/: {root.as_posix()}"
        )
    return root


def _matches_source_checkout(root: pathlib.Path) -> bool:
    """Return whether *root* is a StrongClaw source checkout."""
    return all((root / marker).exists() for marker in STRONGCLAW_REPO_MARKERS)


def _configured_asset_root_override(
    repo_root: pathlib.Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path | None:
    """Return the explicit asset-root override when one was supplied."""
    if repo_root is not None:
        return _resolve_path(repo_root)
    env = os.environ if environ is None else environ
    configured = env.get(ASSET_ROOT_ENV_VAR, "").strip()
    if not configured:
        return None
    return _require_platform_root(_resolve_path(configured))


def require_asset_root(root: pathlib.Path | str) -> pathlib.Path:
    """Resolve and validate one explicit runtime asset-root override."""
    return _require_platform_root(_resolve_path(root))


def resolve_asset_root(
    repo_root: pathlib.Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the effective runtime asset root."""
    override = _configured_asset_root_override(repo_root, environ=environ)
    if override is not None:
        return override
    return _require_platform_root(PACKAGED_ASSET_ROOT)


def resolve_source_checkout_root(
    repo_root: pathlib.Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path | None:
    """Return the active explicit StrongClaw source checkout when one exists."""
    override = _configured_asset_root_override(repo_root, environ=environ)
    if override is None:
        return None
    if _matches_source_checkout(override):
        return override
    return None


def resolve_runtime_layout(
    *,
    repo_root: pathlib.Path | str | None = None,
    home_dir: pathlib.Path | str | None = None,
    runtime_root: pathlib.Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeLayout:
    """Return the full runtime layout for the current invocation."""
    env = os.environ if environ is None else environ
    resolved_home = pathlib.Path.home() if home_dir is None else pathlib.Path(home_dir)
    expanded_home = resolved_home.expanduser().resolve()
    resolved_runtime_root = (
        _resolve_path(runtime_root)
        if runtime_root is not None
        else strongclaw_runtime_root(environ=env)
    )
    uses_isolated_runtime = resolved_runtime_root is not None
    asset_root = resolve_asset_root(repo_root, environ=env)
    strongclaw_data_root = strongclaw_data_dir(home_dir=expanded_home, environ=env)
    strongclaw_config_root = strongclaw_config_dir(home_dir=expanded_home, environ=env)
    strongclaw_state_root = strongclaw_state_dir(home_dir=expanded_home, environ=env)
    strongclaw_log_root = strongclaw_log_dir(home_dir=expanded_home, environ=env)
    openclaw_home_override = env.get("OPENCLAW_HOME", "").strip()
    if openclaw_home_override:
        openclaw_home = _resolve_path(openclaw_home_override)
    elif uses_isolated_runtime:
        assert resolved_runtime_root is not None
        openclaw_home = resolved_runtime_root
    else:
        openclaw_home = expanded_home / ".openclaw"
    openclaw_state_override = env.get("OPENCLAW_STATE_DIR", "").strip()
    if openclaw_state_override:
        openclaw_state_dir = _resolve_path(openclaw_state_override)
    elif uses_isolated_runtime or bool(openclaw_home_override):
        openclaw_state_dir = openclaw_home / ".openclaw"
    else:
        openclaw_state_dir = expanded_home / ".openclaw"
    openclaw_config_override = (
        env.get("OPENCLAW_CONFIG_PATH", "").strip() or env.get("OPENCLAW_CONFIG", "").strip()
    )
    openclaw_config_path = (
        _resolve_path(openclaw_config_override)
        if openclaw_config_override
        else openclaw_state_dir / "openclaw.json"
    )
    openclaw_profile = env.get("OPENCLAW_PROFILE", "").strip() or (
        DEFAULT_DEV_OPENCLAW_PROFILE if uses_isolated_runtime else None
    )
    strongclaw_root = (
        resolved_runtime_root / "strongclaw"
        if resolved_runtime_root is not None
        else strongclaw_data_root
    )
    return RuntimeLayout(
        asset_root=asset_root,
        platform_root=asset_root / PLATFORM_DIR_NAME,
        source_checkout_root=resolve_source_checkout_root(repo_root, environ=env),
        real_home_dir=expanded_home,
        runtime_root=resolved_runtime_root,
        uses_isolated_runtime=uses_isolated_runtime,
        strongclaw_root=strongclaw_root,
        strongclaw_data_root=strongclaw_data_root,
        strongclaw_config_root=strongclaw_config_root,
        strongclaw_state_root=strongclaw_state_root,
        strongclaw_log_root=strongclaw_log_root,
        workspace_root=strongclaw_workspace_dir(home_dir=expanded_home, environ=env),
        upstream_repo_root=strongclaw_upstream_repo_dir(home_dir=expanded_home, environ=env),
        worktrees_root=strongclaw_worktrees_dir(home_dir=expanded_home, environ=env),
        plugin_root=strongclaw_plugins_dir(home_dir=expanded_home, environ=env),
        varlock_env_root=strongclaw_varlock_dir(home_dir=expanded_home, environ=env),
        memory_config_root=strongclaw_memory_config_dir(home_dir=expanded_home, environ=env),
        hypermemory_config_path=strongclaw_memory_config_dir(home_dir=expanded_home, environ=env)
        / "hypermemory.yaml",
        hypermemory_sqlite_config_path=strongclaw_memory_config_dir(
            home_dir=expanded_home, environ=env
        )
        / "hypermemory.sqlite.yaml",
        openclaw_home=openclaw_home,
        openclaw_state_dir=openclaw_state_dir,
        openclaw_config_path=openclaw_config_path,
        openclaw_profile=openclaw_profile,
    )


def resolve_asset_path(
    relative_path: pathlib.Path | str,
    *,
    repo_root: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """Resolve one packaged/source asset path under the runtime asset root."""
    candidate = pathlib.Path(relative_path)
    if candidate.is_absolute():
        return candidate.expanduser().resolve()
    return (resolve_asset_root(repo_root) / candidate).resolve()


def resolve_packaged_platform_path(relative_path: pathlib.Path | str) -> pathlib.Path:
    """Resolve one path under the packaged/source `platform` tree."""
    return resolve_asset_path(pathlib.Path(PLATFORM_DIR_NAME) / pathlib.Path(relative_path))


def mirror_asset_tree(
    source_dir: pathlib.Path,
    target_dir: pathlib.Path,
    *,
    ignore_names: tuple[str, ...] = (),
) -> pathlib.Path:
    """Copy a packaged/source asset tree into a writable target directory."""
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    ignore = None if not ignore_names else shutil.ignore_patterns(*ignore_names)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True, ignore=ignore)
    return target_dir


def resolve_managed_plugin_dir(
    plugin_name: str,
    *,
    home_dir: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """Return the writable managed plugin directory for one plugin."""
    resolved_home = None if home_dir is None else pathlib.Path(home_dir)
    return strongclaw_plugin_dir(plugin_name, home_dir=resolved_home)
