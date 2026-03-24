"""Shared app data and state path helpers for StrongClaw-managed artifacts."""

from __future__ import annotations

import hashlib
import os
import pathlib
import platform
import re
from collections.abc import Mapping

APP_DIR_LINUX = "strongclaw"
APP_DIR_MACOS = "StrongClaw"


def _resolve_os_name(os_name: str | None = None) -> str:
    """Normalize the host OS name for path selection."""
    raw_name = (platform.system() if os_name is None else os_name).lower()
    if raw_name.startswith("darwin") or raw_name == "macos":
        return "darwin"
    return "linux"


def _resolve_home_dir(home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the effective user home directory."""
    base_dir = pathlib.Path.home() if home_dir is None else home_dir
    return base_dir.expanduser().resolve()


def _resolve_override_path(
    override_name: str,
    *,
    home_dir: pathlib.Path,
    environ: Mapping[str, str],
) -> pathlib.Path | None:
    """Return an expanded override path when the caller supplied one."""
    raw_value = environ.get(override_name)
    if raw_value is None or not raw_value.strip():
        return None
    return pathlib.Path(raw_value).expanduser().resolve()


def strongclaw_data_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default StrongClaw data directory."""
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home_dir(home_dir)
    override = _resolve_override_path("STRONGCLAW_DATA_DIR", home_dir=resolved_home, environ=env)
    if override is not None:
        return override
    xdg_data_home = env.get("XDG_DATA_HOME")
    if xdg_data_home:
        return pathlib.Path(xdg_data_home).expanduser().resolve() / APP_DIR_LINUX
    if _resolve_os_name(os_name) == "darwin":
        return resolved_home / "Library" / "Application Support" / APP_DIR_MACOS
    return resolved_home / ".local" / "share" / APP_DIR_LINUX


def strongclaw_state_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default StrongClaw state directory."""
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home_dir(home_dir)
    override = _resolve_override_path("STRONGCLAW_STATE_DIR", home_dir=resolved_home, environ=env)
    if override is not None:
        return override
    xdg_state_home = env.get("XDG_STATE_HOME")
    if xdg_state_home:
        return pathlib.Path(xdg_state_home).expanduser().resolve() / APP_DIR_LINUX
    if _resolve_os_name(os_name) == "darwin":
        return resolved_home / "Library" / "Application Support" / APP_DIR_MACOS / "state"
    return resolved_home / ".local" / "state" / APP_DIR_LINUX


def strongclaw_log_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default StrongClaw log directory."""
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home_dir(home_dir)
    override = _resolve_override_path("STRONGCLAW_LOG_DIR", home_dir=resolved_home, environ=env)
    if override is not None:
        return override
    if _resolve_os_name(os_name) == "darwin":
        return resolved_home / "Library" / "Logs" / APP_DIR_MACOS
    return strongclaw_state_dir(home_dir=resolved_home, environ=env, os_name=os_name) / "logs"


def strongclaw_runs_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default StrongClaw artifact directory for generated run outputs."""
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home_dir(home_dir)
    override = _resolve_override_path("STRONGCLAW_RUNS_DIR", home_dir=resolved_home, environ=env)
    if override is not None:
        return override
    return strongclaw_state_dir(home_dir=resolved_home, environ=env, os_name=os_name) / "runs"


def strongclaw_compose_state_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default compose-sidecar state root."""
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home_dir(home_dir)
    override = _resolve_override_path(
        "STRONGCLAW_COMPOSE_STATE_DIR", home_dir=resolved_home, environ=env
    )
    if override is not None:
        return override
    return strongclaw_state_dir(home_dir=resolved_home, environ=env, os_name=os_name) / "compose"


def strongclaw_repo_local_compose_state_dir(
    repo_root: pathlib.Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the repo-local compose-state directory used by development sidecars."""
    env = os.environ if environ is None else environ
    override = _resolve_override_path(
        "STRONGCLAW_REPO_LOCAL_COMPOSE_STATE_DIR",
        home_dir=_resolve_home_dir(),
        environ=env,
    )
    if override is not None:
        return override
    return repo_root.expanduser().resolve() / "platform" / "compose" / "state"


def strongclaw_lossless_claw_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default install directory for the lossless-claw plugin checkout."""
    env = os.environ if environ is None else environ
    resolved_home = _resolve_home_dir(home_dir)
    override = _resolve_override_path(
        "STRONGCLAW_LOSSLESS_CLAW_DIR", home_dir=resolved_home, environ=env
    )
    if override is not None:
        return override
    return (
        strongclaw_data_dir(home_dir=resolved_home, environ=env, os_name=os_name)
        / "plugins"
        / "lossless-claw"
    )


def strongclaw_qmd_install_dir(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return the default install directory for the QMD package files."""
    return strongclaw_data_dir(home_dir=home_dir, environ=environ, os_name=os_name) / "qmd"


def _slugify(value: str) -> str:
    """Return a stable filesystem-safe token."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return normalized or "scope"


def scoped_state_dir(
    scope_root: pathlib.Path,
    *,
    category: str,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> pathlib.Path:
    """Return a deterministic per-workspace artifact directory."""
    resolved_scope_root = scope_root.expanduser().resolve()
    digest = hashlib.sha256(resolved_scope_root.as_posix().encode("utf-8")).hexdigest()[:12]
    namespace = f"{_slugify(resolved_scope_root.name)}-{digest}"
    return (
        strongclaw_state_dir(home_dir=home_dir, environ=environ, os_name=os_name)
        / "workspaces"
        / namespace
        / category
    )
