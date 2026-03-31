"""Shared runtime helpers for the StrongClaw scripts-to-CLI migration."""

from __future__ import annotations

import dataclasses
import getpass
import json
import os
import pathlib
import platform
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, cast

from clawops.app_paths import (
    strongclaw_compose_state_dir,
    strongclaw_config_dir,
    strongclaw_data_dir,
    strongclaw_log_dir,
    strongclaw_lossless_claw_dir,
    strongclaw_memory_config_dir,
    strongclaw_plugin_dir,
    strongclaw_qmd_install_dir,
    strongclaw_repo_dir,
    strongclaw_repo_local_compose_state_dir,
    strongclaw_runs_dir,
    strongclaw_state_dir,
    strongclaw_varlock_dir,
    strongclaw_workspace_dir,
)
from clawops.memory_profiles import MemoryProfileSpec, resolve_memory_profile
from clawops.platform_compat import (
    DEFAULT_ACPX_VERSION,
    DEFAULT_MANAGED_PROJECT_PYTHON_VERSION,
    DEFAULT_OPENCLAW_VERSION,
    detect_host_platform,
    resolve_memory_plugin_lancedb_version,
)
from clawops.root_detection import DEFAULT_SOURCE_REPO_ROOT
from clawops.runtime_assets import resolve_asset_path, resolve_runtime_layout

DEFAULT_REPO_ROOT: Final[pathlib.Path] = DEFAULT_SOURCE_REPO_ROOT
DEFAULT_PROFILE_NAME = "hypermemory"
DEFAULT_UV_VERSION = "0.10.9"
DEFAULT_VARLOCK_VERSION = "0.5.0"
DEFAULT_OPENCLAW_CONFIG = pathlib.Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_VARLOCK_ENV_RELATIVE = pathlib.Path("platform/configs/varlock")
DEFAULT_VARLOCK_LOCAL_ENV_NAME = ".env.local"
DEFAULT_VARLOCK_PLUGIN_ENV_NAME = ".env.plugins"
DEFAULT_VARLOCK_ENV_TEMPLATE_NAME = ".env.local.example"
DEFAULT_VARLOCK_SCHEMA_NAME = ".env.schema"
DEFAULT_VARLOCK_EXAMPLE_NAMES = (
    DEFAULT_VARLOCK_ENV_TEMPLATE_NAME,
    ".env.ci.example",
    ".env.prod.example",
    DEFAULT_VARLOCK_SCHEMA_NAME,
)
DEFAULT_SETUP_STATE_DIR_NAME = "setup"
DEFAULT_BOOTSTRAP_STATE_NAME = "bootstrap.env"
DEFAULT_DOCKER_REFRESH_STATE_NAME = "docker-refresh.env"
PLACEHOLDER_PREFIXES = ("replace-with-", "changeme", "your-")
PLACEHOLDER_EXACT = {"", "null"}
DEFAULT_QMD_VERSION = "2.0.1"
DEFAULT_LOSSLESS_CLAW_REF = "v0.3.0"
DEFAULT_LOSSLESS_CLAW_REPO = "https://github.com/Martian-Engineering/lossless-claw.git"
DEFAULT_VARLOCK_BIN_DIR = (
    pathlib.Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "varlock" / "bin"
)
LEGACY_VARLOCK_BIN_DIR = pathlib.Path("~/ .varlock/bin".replace(" ", "")).expanduser()


@dataclasses.dataclass(frozen=True, slots=True)
class ExecResult:
    """Structured subprocess result."""

    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    failed_to_start: bool = False

    @property
    def ok(self) -> bool:
        """Return whether the command succeeded."""
        return not self.timed_out and not self.failed_to_start and self.returncode == 0


class CommandError(RuntimeError):
    """Raised when a managed command fails."""

    def __init__(self, message: str, *, result: ExecResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclasses.dataclass(frozen=True, slots=True)
class BootstrapState:
    """Persisted bootstrap completion state."""

    profile: str
    host_os: str
    runtime_user: str
    capabilities: tuple[str, ...]
    completed_at: str


@dataclasses.dataclass(frozen=True, slots=True)
class DockerRefreshState:
    """Persisted Docker shell-refresh state."""

    runtime_user: str
    reason: str
    created_at: str


@dataclasses.dataclass(frozen=True, slots=True)
class DockerBackendDiagnostics:
    """Structured Docker backend reachability diagnostics."""

    docker_cli_installed: bool
    docker_compose_available: bool
    backend_ready: bool
    provider: str | None
    context: str | None
    docker_host: str | None
    info_stdout: str
    info_stderr: str


def resolve_repo_root(repo_root: pathlib.Path | str | None = None) -> pathlib.Path:
    """Return the effective StrongClaw asset root."""
    return resolve_runtime_layout(repo_root=repo_root).asset_root


def resolve_home_dir(home_dir: pathlib.Path | str | None = None) -> pathlib.Path:
    """Return the effective home directory."""
    if home_dir is None:
        return pathlib.Path.home().expanduser().resolve()
    return pathlib.Path(home_dir).expanduser().resolve()


def resolve_profile(profile_name: str | None = None) -> str:
    """Return the active bootstrap profile."""
    configured = profile_name or os.environ.get("OPENCLAW_CONFIG_PROFILE")
    return configured or os.environ.get("STRONGCLAW_DEFAULT_PROFILE", DEFAULT_PROFILE_NAME)


def _resolved_profile_spec(profile_name: str | None = None) -> MemoryProfileSpec | None:
    """Resolve the active profile from the shared registry when available."""
    return resolve_memory_profile(resolve_profile(profile_name))


def profile_requires_qmd(profile_name: str | None = None) -> bool:
    """Return whether the profile requires the QMD asset."""
    spec = _resolved_profile_spec(profile_name)
    return bool(spec is not None and spec.installs_qmd)


def profile_requires_lossless_claw(profile_name: str | None = None) -> bool:
    """Return whether the profile requires the lossless-claw plugin."""
    spec = _resolved_profile_spec(profile_name)
    return bool(spec is not None and spec.installs_lossless_claw)


def profile_requires_hypermemory_backend(profile_name: str | None = None) -> bool:
    """Return whether the profile enables strongclaw-hypermemory."""
    spec = _resolved_profile_spec(profile_name)
    return bool(spec is not None and spec.enables_hypermemory_backend)


def profile_requires_memory_pro_plugin(profile_name: str | None = None) -> bool:
    """Return whether the profile requires the vendored memory-pro plugin."""
    spec = _resolved_profile_spec(profile_name)
    return bool(spec is not None and spec.installs_memory_pro)


def profile_bootstrap_capabilities(profile_name: str | None = None) -> tuple[str, ...]:
    """Return the normalized capability list for a profile."""
    profile = resolve_profile(profile_name)
    capabilities: list[str] = []
    if profile_requires_qmd(profile):
        capabilities.append("qmd")
    if profile_requires_memory_pro_plugin(profile):
        capabilities.append("memory-pro-plugin")
    if profile_requires_lossless_claw(profile):
        capabilities.append("lossless-claw")
    if profile_requires_hypermemory_backend(profile):
        capabilities.append("hypermemory")
    return tuple(capabilities)


def command_exists(command_name: str) -> bool:
    """Return whether *command_name* is on PATH."""
    return shutil.which(command_name) is not None


def resolve_varlock_bin() -> pathlib.Path | None:
    """Return the effective Varlock binary path when available."""
    found = shutil.which("varlock")
    if found:
        return pathlib.Path(found).resolve()
    for candidate in (DEFAULT_VARLOCK_BIN_DIR / "varlock", LEGACY_VARLOCK_BIN_DIR / "varlock"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _timestamp_text() -> str:
    """Return a stable UTC timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _command_display(command: Sequence[str]) -> str:
    """Render a command for diagnostics."""
    return shlex.join(command)


def run_command(
    command: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 30,
    capture_output: bool = True,
    input_text: str | None = None,
    check: bool = False,
) -> ExecResult:
    """Run a subprocess and return a structured result."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    argv = tuple(str(part) for part in command)
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            check=False,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else dict(env),
            text=True,
            input=input_text,
            capture_output=capture_output,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        result = ExecResult(
            argv=argv,
            returncode=None,
            stdout="" if exc.stdout is None else str(exc.stdout),
            stderr="" if exc.stderr is None else str(exc.stderr),
            duration_ms=int((time.perf_counter() - start) * 1000),
            timed_out=True,
        )
        if check:
            raise CommandError(
                f"command timed out: {_command_display(argv)}",
                result=result,
            ) from exc
        return result
    except OSError as exc:
        result = ExecResult(
            argv=argv,
            returncode=None,
            stdout="",
            stderr=str(exc),
            duration_ms=int((time.perf_counter() - start) * 1000),
            failed_to_start=True,
        )
        if check:
            raise CommandError(
                f"command failed to start: {_command_display(argv)}",
                result=result,
            ) from exc
        return result

    result = ExecResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
    if check and not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise CommandError(f"{detail}: {_command_display(argv)}", result=result)
    return result


def run_command_inherited(
    command: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int | None = 1800,
) -> int:
    """Run a subprocess with inherited stdio."""
    argv = [str(part) for part in command]
    cwd_value = None if cwd is None else str(cwd)
    env_value = None if env is None else dict(env)
    if timeout_seconds is None:
        completed = subprocess.run(
            argv,
            check=False,
            cwd=cwd_value,
            env=env_value,
        )
        return int(completed.returncode)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive when provided")
    completed = subprocess.run(
        argv,
        check=False,
        cwd=cwd_value,
        env=env_value,
        timeout=timeout_seconds,
    )
    return int(completed.returncode)


def expand_user_path(path_text: str, *, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Expand a shell-style path containing `~`."""
    resolved_home = resolve_home_dir(home_dir)
    if path_text == "~":
        return resolved_home
    if path_text.startswith("~/"):
        return resolved_home / path_text[2:]
    return pathlib.Path(path_text).expanduser().resolve()


def bootstrap_state_dir() -> pathlib.Path:
    """Return the setup-state directory."""
    override = os.environ.get("OPENCLAW_SETUP_STATE_DIR")
    if override:
        return expand_user_path(override)
    return strongclaw_state_dir() / DEFAULT_SETUP_STATE_DIR_NAME


def bootstrap_state_file() -> pathlib.Path:
    """Return the bootstrap state file path."""
    override = os.environ.get("OPENCLAW_BOOTSTRAP_STATE_FILE")
    if override:
        return expand_user_path(override)
    return bootstrap_state_dir() / DEFAULT_BOOTSTRAP_STATE_NAME


def docker_refresh_state_file() -> pathlib.Path:
    """Return the Docker refresh state file path."""
    override = os.environ.get("OPENCLAW_DOCKER_REFRESH_STATE_FILE")
    if override:
        return expand_user_path(override)
    return bootstrap_state_dir() / DEFAULT_DOCKER_REFRESH_STATE_NAME


def _read_key_value_file(path: pathlib.Path) -> dict[str, str]:
    """Read a simple KEY=VALUE file."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key] = value
    return values


def _write_key_value_file(path: pathlib.Path, values: Mapping[str, str]) -> None:
    """Write a simple KEY=VALUE file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_bootstrap_state() -> BootstrapState | None:
    """Load the persisted bootstrap state."""
    values = _read_key_value_file(bootstrap_state_file())
    if not values:
        return None
    capabilities_text = values.get("CAPABILITIES", "")
    return BootstrapState(
        profile=values.get("PROFILE", resolve_profile()),
        host_os=values.get("HOST_OS", platform.system()),
        runtime_user=values.get("RUNTIME_USER", getpass.getuser()),
        capabilities=tuple(part for part in capabilities_text.split() if part),
        completed_at=values.get("COMPLETED_AT", ""),
    )


def bootstrap_state_ready() -> bool:
    """Return whether bootstrap completion is recorded."""
    return bootstrap_state_file().exists()


def mark_bootstrap_complete(
    *,
    profile: str,
    host_os: str,
    runtime_user: str,
    capabilities: Iterable[str] | None = None,
) -> None:
    """Persist bootstrap completion state."""
    resolved_capabilities = tuple(capabilities or profile_bootstrap_capabilities(profile))
    _write_key_value_file(
        bootstrap_state_file(),
        {
            "PROFILE": profile,
            "HOST_OS": host_os,
            "RUNTIME_USER": runtime_user,
            "CAPABILITIES": " ".join(resolved_capabilities),
            "COMPLETED_AT": _timestamp_text(),
        },
    )


def load_docker_refresh_state() -> DockerRefreshState | None:
    """Load the persisted Docker refresh state."""
    values = _read_key_value_file(docker_refresh_state_file())
    if not values:
        return None
    return DockerRefreshState(
        runtime_user=values.get("RUNTIME_USER", getpass.getuser()),
        reason=values.get("REASON", ""),
        created_at=values.get("CREATED_AT", ""),
    )


def docker_shell_refresh_required() -> bool:
    """Return whether the operator must start a fresh login shell."""
    return docker_refresh_state_file().exists()


def mark_docker_shell_refresh_required(runtime_user: str, reason: str) -> None:
    """Persist Docker refresh state."""
    _write_key_value_file(
        docker_refresh_state_file(),
        {
            "RUNTIME_USER": runtime_user,
            "REASON": reason,
            "CREATED_AT": _timestamp_text(),
        },
    )


def clear_docker_shell_refresh_required() -> None:
    """Remove Docker refresh state when no longer needed."""
    docker_refresh_state_file().unlink(missing_ok=True)


def is_placeholder_value(value: str | None) -> bool:
    """Return whether a value is unset or obviously placeholder text."""
    candidate = "" if value is None else value.strip()
    if candidate in PLACEHOLDER_EXACT:
        return True
    if candidate.startswith("<") and candidate.endswith(">"):
        return True
    lowered = candidate.casefold()
    return lowered.startswith(PLACEHOLDER_PREFIXES)


def value_is_effective(value: str | None) -> bool:
    """Return whether a value is non-empty and not placeholder text."""
    return not is_placeholder_value(value)


def generate_secret_value() -> str:
    """Return a StrongClaw-managed random secret."""
    return secrets.token_urlsafe(32)


def varlock_env_dir(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the Varlock env directory."""
    env = os.environ if environ is None else environ
    override = env.get("OPENCLAW_VARLOCK_ENV_PATH") or env.get("VARLOCK_ENV_DIR")
    if override:
        return expand_user_path(override, home_dir=home_dir)
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir, environ=env)
    managed_dir = strongclaw_varlock_dir(home_dir=layout.home_dir, environ=env)
    legacy_dir = layout.asset_root / DEFAULT_VARLOCK_ENV_RELATIVE
    if layout.uses_isolated_runtime:
        materialize_runtime_varlock_assets(repo_root, home_dir=layout.home_dir)
        return managed_dir
    if (legacy_dir / DEFAULT_VARLOCK_LOCAL_ENV_NAME).exists() or (
        legacy_dir / DEFAULT_VARLOCK_PLUGIN_ENV_NAME
    ).exists():
        return legacy_dir
    materialize_runtime_varlock_assets(repo_root, home_dir=layout.home_dir)
    if (managed_dir / DEFAULT_VARLOCK_LOCAL_ENV_NAME).exists() or (
        managed_dir / DEFAULT_VARLOCK_PLUGIN_ENV_NAME
    ).exists():
        return managed_dir
    return managed_dir


def varlock_local_env_file(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the local Varlock env file path."""
    env = os.environ if environ is None else environ
    override = env.get("VARLOCK_LOCAL_ENV_FILE")
    if override:
        return expand_user_path(override, home_dir=home_dir)
    return (
        varlock_env_dir(repo_root, home_dir=home_dir, environ=environ)
        / DEFAULT_VARLOCK_LOCAL_ENV_NAME
    )


def varlock_plugin_env_file(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the plugin-backed Varlock env overlay path."""
    env = os.environ if environ is None else environ
    override = env.get("VARLOCK_PLUGIN_ENV_FILE")
    if override:
        return expand_user_path(override, home_dir=home_dir)
    return (
        varlock_env_dir(repo_root, home_dir=home_dir, environ=environ)
        / DEFAULT_VARLOCK_PLUGIN_ENV_NAME
    )


def varlock_env_template_file(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Return the shipped local env template path."""
    override = os.environ.get("VARLOCK_ENV_TEMPLATE")
    if override:
        return expand_user_path(override, home_dir=home_dir)
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir)
    return layout.asset_root / DEFAULT_VARLOCK_ENV_RELATIVE / DEFAULT_VARLOCK_ENV_TEMPLATE_NAME


def materialize_runtime_varlock_assets(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Mirror the shipped Varlock schema/examples into the managed config directory."""
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir)
    managed_dir = strongclaw_varlock_dir(home_dir=layout.home_dir)
    template_dir = layout.asset_root / DEFAULT_VARLOCK_ENV_RELATIVE
    if not all((template_dir / file_name).is_file() for file_name in DEFAULT_VARLOCK_EXAMPLE_NAMES):
        template_dir = resolve_asset_path(DEFAULT_VARLOCK_ENV_RELATIVE)
    managed_dir.mkdir(parents=True, exist_ok=True)
    for file_name in DEFAULT_VARLOCK_EXAMPLE_NAMES:
        source_path = template_dir / file_name
        if not source_path.is_file():
            raise FileNotFoundError(f"Missing shipped Varlock asset: {source_path}")
        target_path = managed_dir / file_name
        shutil.copy2(source_path, target_path)
    return managed_dir


def load_env_assignments(path: pathlib.Path) -> dict[str, str]:
    """Load a dotenv-style assignment file."""
    return _read_key_value_file(path)


def write_env_assignments(path: pathlib.Path, values: Mapping[str, str]) -> None:
    """Write a dotenv-style assignment file."""
    _write_key_value_file(path, values)
    if path.exists():
        path.chmod(0o600)


def set_env_assignment(path: pathlib.Path, key: str, value: str) -> None:
    """Update or add one assignment in a dotenv-style file."""
    values = load_env_assignments(path)
    values[key] = value
    write_env_assignments(path, values)


def clear_env_assignment(path: pathlib.Path, key: str) -> None:
    """Clear an assignment value while keeping the key present."""
    set_env_assignment(path, key, "")


def merge_env_template(
    *,
    target_path: pathlib.Path,
    template_path: pathlib.Path,
) -> tuple[dict[str, str], list[str]]:
    """Merge missing assignments from a template file into the target."""
    target_values = load_env_assignments(target_path)
    template_values = load_env_assignments(template_path)
    merged_keys: list[str] = []
    for key, value in template_values.items():
        if key in target_values:
            continue
        target_values[key] = value
        merged_keys.append(key)
    if merged_keys:
        write_env_assignments(target_path, target_values)
    return target_values, merged_keys


def varlock_available() -> bool:
    """Return whether the Varlock CLI is on PATH."""
    return resolve_varlock_bin() is not None


def build_varlock_prefix(repo_root: pathlib.Path) -> list[str]:
    """Build the Varlock command prefix for the repo-local env."""
    varlock_bin = resolve_varlock_bin()
    if varlock_bin is None:
        raise CommandError("Varlock is required for this operation.")
    return [
        str(varlock_bin),
        "run",
        "--path",
        str(varlock_env_dir(repo_root)),
        "--",
    ]


def wrap_command_with_varlock(repo_root: pathlib.Path, command: Sequence[str]) -> list[str]:
    """Wrap a command in `varlock run` when available."""
    if varlock_available() and varlock_env_dir(repo_root).is_dir():
        return [*build_varlock_prefix(repo_root), *[str(part) for part in command]]
    return [str(part) for part in command]


def run_varlock_command(
    repo_root: pathlib.Path,
    command: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 30,
    check: bool = False,
) -> ExecResult:
    """Run a command through Varlock when available."""
    return run_command(
        wrap_command_with_varlock(repo_root, command),
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        check=check,
    )


def default_openclaw_config_path(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the default rendered OpenClaw config path."""
    return resolve_runtime_layout(home_dir=home_dir, environ=environ).openclaw_config_path


def resolve_openclaw_config_path(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the rendered OpenClaw config path."""
    env = os.environ if environ is None else environ
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir, environ=env)
    config_path = env.get("OPENCLAW_CONFIG_PATH", "").strip()
    if config_path:
        return expand_user_path(config_path, home_dir=home_dir)
    legacy_config = env.get("OPENCLAW_CONFIG", "").strip()
    if legacy_config:
        return expand_user_path(legacy_config, home_dir=home_dir)
    if layout.uses_isolated_runtime:
        return layout.openclaw_config_path
    local_env = load_env_assignments(
        varlock_local_env_file(repo_root, home_dir=home_dir, environ=env)
    )
    local_config_path = local_env.get("OPENCLAW_CONFIG_PATH", "").strip()
    if local_config_path:
        return expand_user_path(local_config_path, home_dir=home_dir)
    legacy_local_config = local_env.get("OPENCLAW_CONFIG", "").strip()
    if legacy_local_config:
        return expand_user_path(legacy_local_config, home_dir=home_dir)
    return layout.openclaw_config_path


def openclaw_available() -> bool:
    """Return whether the OpenClaw CLI is on PATH."""
    return command_exists("openclaw")


def require_openclaw(context: str) -> None:
    """Require the OpenClaw CLI to be present."""
    if openclaw_available():
        return
    raise CommandError(f"{context} Run `clawops bootstrap` to install OpenClaw.")


def run_openclaw_command(
    repo_root: pathlib.Path,
    arguments: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    timeout_seconds: int = 30,
    check: bool = False,
) -> ExecResult:
    """Run the OpenClaw CLI with repo-local Varlock wrapping when available."""
    require_openclaw("This operation requires the OpenClaw CLI.")
    return run_varlock_command(
        repo_root,
        ["openclaw", *arguments],
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        check=check,
    )


def load_openclaw_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Load the rendered OpenClaw config as a mapping."""
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected a mapping in {config_path}")
    return cast(dict[str, Any], payload)


def _nested_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """Return a nested mapping value when present."""
    value = payload.get(key)
    if isinstance(value, Mapping):
        return cast(Mapping[str, Any], value)
    return None


def rendered_openclaw_memory_backend(config_path: pathlib.Path) -> str:
    """Return the configured OpenClaw memory backend."""
    memory = _nested_mapping(load_openclaw_config(config_path), "memory")
    if memory is None:
        return ""
    value = memory.get("backend")
    return value if isinstance(value, str) else ""


def rendered_openclaw_memory_slot(config_path: pathlib.Path) -> str:
    """Return the configured OpenClaw memory slot."""
    plugins = _nested_mapping(load_openclaw_config(config_path), "plugins")
    if plugins is None:
        return ""
    slots = _nested_mapping(plugins, "slots")
    if slots is None:
        return ""
    value = slots.get("memory")
    return value if isinstance(value, str) else ""


def rendered_openclaw_context_engine_slot(config_path: pathlib.Path) -> str:
    """Return the configured OpenClaw context-engine slot."""
    plugins = _nested_mapping(load_openclaw_config(config_path), "plugins")
    if plugins is None:
        return ""
    slots = _nested_mapping(plugins, "slots")
    if slots is None:
        return ""
    value = slots.get("contextEngine")
    return value if isinstance(value, str) else ""


def rendered_openclaw_hypermemory_config_path(config_path: pathlib.Path) -> pathlib.Path | None:
    """Return the strongclaw-hypermemory config path when configured."""
    plugins = _nested_mapping(load_openclaw_config(config_path), "plugins")
    if plugins is None:
        return None
    entries = _nested_mapping(plugins, "entries")
    if entries is None:
        return None
    plugin_entry = entries.get("strongclaw-hypermemory")
    if not isinstance(plugin_entry, Mapping):
        return None
    config_mapping = _nested_mapping(cast(Mapping[str, Any], plugin_entry), "config")
    if config_mapping is None:
        return None
    raw_value = config_mapping.get("configPath")
    if not isinstance(raw_value, str) or not raw_value:
        return None
    return pathlib.Path(raw_value).expanduser().resolve()


def rendered_openclaw_lossless_plugin_path(config_path: pathlib.Path) -> pathlib.Path | None:
    """Return the first configured lossless-claw plugin path."""
    plugins = _nested_mapping(load_openclaw_config(config_path), "plugins")
    if plugins is None:
        return None
    load_mapping = _nested_mapping(plugins, "load")
    if load_mapping is None:
        return None
    paths_value = load_mapping.get("paths")
    if not isinstance(paths_value, list):
        return None
    for raw_entry in cast(Sequence[object], paths_value):
        if isinstance(raw_entry, str) and "lossless-claw" in raw_entry:
            return pathlib.Path(raw_entry).expanduser().resolve()
    return None


def rendered_openclaw_uses_qmd(config_path: pathlib.Path) -> bool:
    """Return whether the rendered config uses QMD."""
    return rendered_openclaw_memory_backend(config_path) == "qmd"


def rendered_openclaw_uses_lossless_claw(config_path: pathlib.Path) -> bool:
    """Return whether the rendered config uses lossless-claw."""
    return rendered_openclaw_context_engine_slot(config_path) == "lossless-claw"


def rendered_openclaw_uses_hypermemory(config_path: pathlib.Path) -> bool:
    """Return whether the rendered config uses strongclaw-hypermemory."""
    return rendered_openclaw_memory_slot(config_path) == "strongclaw-hypermemory"


def docker_cli_installed() -> bool:
    """Return whether the Docker CLI is on PATH."""
    return command_exists("docker")


def docker_compose_available() -> bool:
    """Return whether `docker compose` is available."""
    if not docker_cli_installed():
        return False
    return run_command(["docker", "compose", "version"], timeout_seconds=10).ok


def _docker_context_name() -> str | None:
    """Return the active Docker context when available."""
    if not docker_cli_installed():
        return None
    result = run_command(["docker", "context", "show"], timeout_seconds=10)
    if not result.ok:
        return None
    context = result.stdout.strip()
    return context or None


def _infer_docker_provider(
    *,
    host_os: str,
    context: str | None,
    docker_host: str | None,
) -> str | None:
    """Infer the Docker runtime provider from runtime signals."""
    haystack = " ".join(part for part in (context, docker_host) if part).casefold()
    if "orbstack" in haystack:
        return "OrbStack"
    if "colima" in haystack:
        return "Colima"
    if "rancher" in haystack:
        return "Rancher Desktop"
    if "desktop" in haystack:
        return "Docker Desktop"
    provider = detect_docker_runtime_provider(host_os)
    return None if provider == "docker-compose" else provider


def docker_backend_diagnostics() -> DockerBackendDiagnostics:
    """Return structured diagnostics for Docker backend reachability."""
    cli_installed = docker_cli_installed()
    compose_installed = docker_compose_available() if cli_installed else False
    context = _docker_context_name() if cli_installed else None
    docker_host = os.environ.get("DOCKER_HOST")
    info_result = (
        run_command(["docker", "info"], timeout_seconds=15)
        if cli_installed and compose_installed
        else ExecResult(
            argv=("docker", "info"), returncode=None, stdout="", stderr="", duration_ms=0
        )
    )
    provider = _infer_docker_provider(
        host_os=platform.system(),
        context=context,
        docker_host=docker_host,
    )
    return DockerBackendDiagnostics(
        docker_cli_installed=cli_installed,
        docker_compose_available=compose_installed,
        backend_ready=cli_installed and compose_installed and info_result.ok,
        provider=provider,
        context=context,
        docker_host=docker_host,
        info_stdout=info_result.stdout.strip(),
        info_stderr=info_result.stderr.strip(),
    )


def docker_backend_ready() -> bool:
    """Return whether the Docker backend is reachable."""
    return docker_backend_diagnostics().backend_ready


def detect_docker_runtime_provider(host_os: str) -> str | None:
    """Return the detected runtime provider name."""
    if docker_cli_installed():
        return "docker"
    if command_exists("docker-compose"):
        return "docker-compose"
    normalized = host_os.casefold()
    if normalized == "darwin":
        if command_exists("orb") or pathlib.Path("/Applications/OrbStack.app").exists():
            return "OrbStack"
        if command_exists("rdctl") or pathlib.Path("/Applications/Rancher Desktop.app").exists():
            return "Rancher Desktop"
        if command_exists("colima"):
            return "Colima"
        if pathlib.Path("/Applications/Docker.app").exists():
            return "Docker Desktop"
        return None
    if normalized == "linux":
        if command_exists("rdctl"):
            return "Rancher Desktop"
        if command_exists("podman"):
            return "Podman"
        if command_exists("colima"):
            return "Colima"
        if command_exists("nerdctl"):
            return "containerd/nerdctl"
    return None


def docker_runtime_enable_guidance(provider: str) -> str:
    """Return the operator guidance for a detected runtime provider."""
    guidance = {
        "Docker Desktop": "Launch Docker Desktop once so it can provision `docker` and `docker compose`, then rerun bootstrap.",
        "OrbStack": "Open OrbStack and enable its Docker CLI integration, then rerun bootstrap.",
        "Rancher Desktop": "Enable the Docker/Moby socket plus CLI integration in Rancher Desktop, then rerun bootstrap.",
        "Colima": "Start Colima with Docker socket support and ensure `docker` plus `docker compose` are on PATH, then rerun bootstrap.",
        "Podman": "Expose Podman through a Docker-compatible `docker` CLI with compose support, then rerun bootstrap.",
        "containerd/nerdctl": "Expose a Docker-compatible `docker` CLI with compose support for nerdctl/containerd, then rerun bootstrap.",
        "docker-compose": "Install a Docker-compatible `docker` CLI that provides `docker compose`, then rerun bootstrap.",
        "docker": "Install or enable the Docker compose plugin, then rerun bootstrap.",
    }
    return guidance.get(
        provider,
        "Enable the runtime's Docker-compatible CLI integration (`docker` plus `docker compose`), then rerun bootstrap.",
    )


def ensure_docker_backend_ready() -> None:
    """Raise when Docker is not ready for sidecar operations."""
    diagnostics = docker_backend_diagnostics()
    if diagnostics.backend_ready:
        clear_docker_shell_refresh_required()
        return
    if not diagnostics.docker_cli_installed:
        raise CommandError(
            "A Docker-compatible runtime is required to activate StrongClaw sidecars."
        )
    if not diagnostics.docker_compose_available:
        raise CommandError(
            "docker is installed but `docker compose` is unavailable. Install or enable a compatible compose plugin."
        )
    refresh_state = load_docker_refresh_state()
    if refresh_state is not None:
        raise CommandError(
            "Docker access was granted during bootstrap, but this shell has not picked up the new group membership yet. "
            f"Start a fresh login shell as {refresh_state.runtime_user}, then rerun the operation."
        )
    details: list[str] = []
    if diagnostics.provider is not None:
        details.append(f"Detected runtime: {diagnostics.provider}.")
    if diagnostics.context is not None:
        details.append(f"Active docker context: {diagnostics.context}.")
    if diagnostics.docker_host:
        details.append(f"DOCKER_HOST={diagnostics.docker_host}.")
    if diagnostics.info_stderr:
        details.append(f"`docker info` stderr: {diagnostics.info_stderr}.")
    elif diagnostics.info_stdout:
        details.append(f"`docker info` output: {diagnostics.info_stdout}.")
    if diagnostics.provider is not None:
        details.append(docker_runtime_enable_guidance(diagnostics.provider))
    raise CommandError(
        "Docker is installed but the backend is not reachable from this shell. " + " ".join(details)
    )


def repair_linux_runtime_user_docker_access(runtime_user: str) -> None:
    """Ensure the runtime user can talk to Docker on Linux."""
    group_result = run_command(["getent", "group", "docker"], timeout_seconds=10)
    if not group_result.ok:
        raise CommandError("docker group is missing after Docker install", result=group_result)
    groups_result = run_command(["id", "-nG", runtime_user], timeout_seconds=10, check=True)
    group_names = groups_result.stdout.split()
    membership_added = False
    if "docker" not in group_names:
        run_command(
            ["sudo", "usermod", "-aG", "docker", runtime_user], timeout_seconds=30, check=True
        )
        mark_docker_shell_refresh_required(runtime_user, "docker-group-membership-updated")
        membership_added = True
    if command_exists("systemctl"):
        run_command(["sudo", "systemctl", "enable", "--now", "docker.service"], timeout_seconds=60)
    if docker_backend_ready():
        clear_docker_shell_refresh_required()
        return
    if membership_added:
        return
    raise CommandError("Docker was installed but is still not reachable from the current shell.")


def managed_python(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the preferred Python executable for managed clawops commands."""
    resolved_repo_root = repo_root.expanduser().resolve()
    source_venv_python = resolved_repo_root / ".venv" / "bin" / "python"
    if source_venv_python.is_file():
        return source_venv_python
    return pathlib.Path(sys.executable)


def managed_clawops_command(repo_root: pathlib.Path, *arguments: str) -> list[str]:
    """Return a `python -m clawops ...` command in the project venv."""
    return [str(managed_python(repo_root)), "-m", "clawops", *arguments]


def host_platform_record(
    *, os_name: str | None = None, architecture: str | None = None
) -> dict[str, object]:
    """Return the normalized host compatibility record."""
    host = detect_host_platform(os_name=os_name, architecture=architecture)
    return {
        "host_os": host.os_name,
        "host_arch": host.architecture,
        "preferred_project_python_version": DEFAULT_MANAGED_PROJECT_PYTHON_VERSION,
        "openclaw_version": DEFAULT_OPENCLAW_VERSION,
        "acpx_version": DEFAULT_ACPX_VERSION,
        "memory_plugin_lancedb_version": resolve_memory_plugin_lancedb_version(host),
    }


def resolve_openclaw_state_dir(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """Return the configured OpenClaw state directory."""
    env = os.environ if environ is None else environ
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir, environ=env)
    override = env.get("OPENCLAW_STATE_DIR", "").strip()
    if override:
        return expand_user_path(override, home_dir=home_dir)
    if layout.uses_isolated_runtime:
        return layout.openclaw_state_dir
    local_env = load_env_assignments(
        varlock_local_env_file(repo_root, home_dir=home_dir, environ=env)
    )
    raw_value = local_env.get("OPENCLAW_STATE_DIR", "").strip()
    if raw_value:
        return expand_user_path(raw_value, home_dir=home_dir)
    return layout.openclaw_state_dir


def resolve_runtime_user(repo_root: pathlib.Path) -> str:
    """Return the configured runtime user."""
    local_env = load_env_assignments(varlock_local_env_file(repo_root))
    return local_env.get("OPENCLAW_CONTROL_USER") or getpass.getuser()


def resolve_runs_dir(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Return the default runs directory."""
    return strongclaw_runs_dir(home_dir=home_dir)


def resolve_repo_local_compose_state_dir(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the repo-local compose-state directory."""
    return strongclaw_repo_local_compose_state_dir(repo_root)


def resolve_compose_state_dir() -> pathlib.Path:
    """Return the global compose-state directory."""
    return strongclaw_compose_state_dir()


def ensure_common_state_roots(
    *,
    home_dir: pathlib.Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Create the shared StrongClaw data and state roots."""
    env = os.environ if environ is None else environ
    roots = (
        strongclaw_data_dir(home_dir=home_dir, environ=env),
        strongclaw_config_dir(home_dir=home_dir, environ=env),
        strongclaw_state_dir(home_dir=home_dir, environ=env),
        strongclaw_log_dir(home_dir=home_dir, environ=env),
        strongclaw_compose_state_dir(home_dir=home_dir, environ=env),
        strongclaw_qmd_install_dir(home_dir=home_dir, environ=env),
        strongclaw_lossless_claw_dir(home_dir=home_dir, environ=env),
        strongclaw_memory_config_dir(home_dir=home_dir, environ=env),
        strongclaw_varlock_dir(home_dir=home_dir, environ=env),
        strongclaw_workspace_dir(home_dir=home_dir, environ=env),
        strongclaw_repo_dir(home_dir=home_dir, environ=env),
        strongclaw_plugin_dir("memory-lancedb-pro", home_dir=home_dir, environ=env),
    )
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
