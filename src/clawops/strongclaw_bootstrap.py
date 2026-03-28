"""Bootstrap the StrongClaw host and managed project environment."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import shutil
import sys
import time
from collections.abc import Sequence

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.platform_compat import detect_host_platform, resolve_memory_plugin_lancedb_version
from clawops.runtime_assets import (
    mirror_asset_tree,
    resolve_asset_path,
    resolve_managed_plugin_dir,
    resolve_runtime_layout,
)
from clawops.strongclaw_runtime import (
    DEFAULT_ACPX_VERSION,
    DEFAULT_LOSSLESS_CLAW_REF,
    DEFAULT_LOSSLESS_CLAW_REPO,
    DEFAULT_OPENCLAW_VERSION,
    DEFAULT_QMD_VERSION,
    DEFAULT_UV_VERSION,
    DEFAULT_VARLOCK_VERSION,
    CommandError,
    command_exists,
    detect_docker_runtime_provider,
    docker_cli_installed,
    docker_compose_available,
    docker_runtime_enable_guidance,
    ensure_common_state_roots,
    managed_clawops_command,
    mark_bootstrap_complete,
    profile_requires_lossless_claw,
    profile_requires_memory_pro_plugin,
    profile_requires_qmd,
    repair_linux_runtime_user_docker_access,
    resolve_home_dir,
    resolve_profile,
    resolve_runtime_user,
    resolve_varlock_bin,
    run_command,
    run_command_inherited,
    strongclaw_lossless_claw_dir,
    strongclaw_qmd_install_dir,
)

EXPECTED_QMD_BIN = pathlib.Path.home() / ".bun" / "bin" / "qmd"
DEFAULT_QMD_PACKAGE = f"@tobilu/qmd@{DEFAULT_QMD_VERSION}"
_UV_SYNC_MAX_ATTEMPTS = 3
_UV_SYNC_RETRY_DELAY_SECONDS = 5


def _stream_checked(
    command: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 1800,
) -> None:
    """Run a command with inherited stdio and require success."""
    returncode = run_command_inherited(
        command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    if returncode != 0:
        raise CommandError(f"command failed with exit code {returncode}: {' '.join(command)}")


def _ensure_brew_formula(formula_name: str) -> None:
    """Install a Homebrew formula when required."""
    _stream_checked(["brew", "install", formula_name], timeout_seconds=1800)


def _ensure_command_or_brew(command_name: str, formula_name: str) -> None:
    """Ensure a command exists, installing its Homebrew formula if needed."""
    if command_exists(command_name):
        return
    _ensure_brew_formula(formula_name)


def _python_satisfies_minimum() -> bool:
    """Return whether python3 >= 3.12 is available."""
    if not command_exists("python3"):
        return False
    return run_command(
        ["python3", "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
        timeout_seconds=10,
    ).ok


def _ensure_python_runtime_darwin() -> None:
    """Ensure macOS has a supported Python runtime."""
    if _python_satisfies_minimum():
        return
    _ensure_brew_formula("python")
    if not _python_satisfies_minimum():
        raise CommandError("python3 >= 3.12 is required")


def _resolve_node_command() -> str | None:
    """Return the available Node.js executable name."""
    for candidate in ("node", "nodejs"):
        if command_exists(candidate):
            return candidate
    return None


def _node_satisfies_minimum() -> bool:
    """Return whether Node.js >= 22.16 is available."""
    node_command = _resolve_node_command()
    if node_command is None:
        return False
    result = run_command(
        [
            node_command,
            "-e",
            "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 16) ? 0 : 1);",
        ],
        timeout_seconds=10,
    )
    return result.ok


def _ensure_node_runtime_darwin() -> None:
    """Ensure macOS has a supported Node.js runtime."""
    if _node_satisfies_minimum():
        return
    _ensure_brew_formula("node")
    if not _node_satisfies_minimum():
        raise CommandError("node >= 22.16 is required")


def _ensure_node_runtime_linux() -> None:
    """Ensure Linux has a supported Node.js runtime."""
    if _node_satisfies_minimum():
        return
    if not command_exists("sudo"):
        raise CommandError("sudo is required to install node >= 22.16 on Linux")
    if not command_exists("apt-get"):
        raise CommandError("apt-get is required to install node >= 22.16 on Linux")
    _stream_checked(
        ["bash", "-lc", "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -"],
        timeout_seconds=1800,
    )
    _stream_checked(["sudo", "apt-get", "install", "-y", "nodejs"], timeout_seconds=1800)
    if not _node_satisfies_minimum():
        raise CommandError("node >= 22.16 is required")


def resolve_uv_binary(*, home_dir: pathlib.Path | None = None) -> pathlib.Path | None:
    """Return the uv binary when available."""
    found = shutil.which("uv")
    if found:
        return pathlib.Path(found).resolve()
    candidate = resolve_home_dir(home_dir) / ".local" / "bin" / "uv"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    return None


def ensure_uv_installed(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Ensure `uv` is installed and return its path."""
    existing = resolve_uv_binary(home_dir=home_dir)
    if existing is not None:
        return existing
    resolved_home = resolve_home_dir(home_dir)
    install_dir = resolved_home / ".local" / "bin"
    install_dir.mkdir(parents=True, exist_ok=True)
    command = (
        f'curl -LsSf "https://astral.sh/uv/{DEFAULT_UV_VERSION}/install.sh" '
        f'| env UV_UNMANAGED_INSTALL="{install_dir}" sh'
    )
    _stream_checked(["bash", "-lc", command], timeout_seconds=1800)
    installed = resolve_uv_binary(home_dir=resolved_home)
    if installed is None:
        raise CommandError("uv install failed")
    return installed


def current_varlock_version() -> str | None:
    """Return the installed Varlock version."""
    varlock_bin = resolve_varlock_bin()
    if varlock_bin is None:
        return None
    result = run_command([str(varlock_bin), "--version"], timeout_seconds=10)
    if not result.ok:
        return None
    return result.stdout.strip().split()[-1]


def ensure_varlock_installed(expected_version: str = DEFAULT_VARLOCK_VERSION) -> pathlib.Path:
    """Ensure Varlock is installed at the expected version."""
    installed_version = current_varlock_version()
    if installed_version == expected_version:
        resolved = resolve_varlock_bin()
        if resolved is None:
            raise CommandError("Varlock binary disappeared after version check")
        return resolved

    install_command = (
        "set -euo pipefail; "
        f"curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 https://varlock.dev/install.sh "
        f'| sh -s -- --force-no-brew --version="{expected_version}"'
    )
    last_error: CommandError | None = None
    for attempt in range(1, 4):
        try:
            _stream_checked(["bash", "-lc", install_command], timeout_seconds=1800)
            last_error = None
            break
        except CommandError as err:
            last_error = err
            if attempt >= 3:
                break
            time.sleep(5 * attempt)
    if last_error is not None:
        raise last_error
    installed_version = current_varlock_version()
    if installed_version != expected_version:
        raise CommandError(
            f"expected varlock {expected_version}, but found {installed_version or 'unavailable'}"
        )
    resolved = resolve_varlock_bin()
    if resolved is None:
        raise CommandError("Varlock install completed without a usable binary")
    return resolved


def ensure_docker_compatible_runtime(host_os: str) -> bool:
    """Ensure a compatible Docker runtime exists. Returns True if bootstrap installed it."""
    if docker_compose_available():
        return False
    runtime_provider = detect_docker_runtime_provider(host_os)
    if docker_cli_installed():
        raise CommandError(
            "docker is installed but `docker compose` is unavailable. "
            + docker_runtime_enable_guidance(runtime_provider or "docker")
        )
    if runtime_provider is not None:
        raise CommandError(
            f"detected {runtime_provider}, but StrongClaw requires `docker` plus `docker compose` on PATH. "
            + docker_runtime_enable_guidance(runtime_provider)
        )
    normalized = host_os.casefold()
    if normalized == "darwin":
        if not command_exists("brew"):
            raise CommandError("Homebrew is required to install Docker Desktop on macOS")
        _stream_checked(["brew", "install", "--cask", "docker"], timeout_seconds=3600)
    elif normalized == "linux":
        if not command_exists("sudo"):
            raise CommandError("sudo is required to install Docker on Linux")
        if not command_exists("apt-get"):
            raise CommandError("apt-get is required to install Docker on Linux")
        _stream_checked(
            ["sudo", "apt-get", "install", "-y", "docker.io", "docker-compose-plugin"],
            timeout_seconds=3600,
        )
    else:
        raise CommandError(f"unsupported host OS for Docker installation: {host_os}")
    if not docker_compose_available():
        if normalized == "darwin":
            raise CommandError("Docker Desktop was installed but the docker CLI is not ready yet")
        raise CommandError("Docker was installed but `docker compose` is still unavailable")
    return True


def install_qmd_asset(*, home_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Install the QMD CLI asset and return the wrapper path."""
    resolved_home = resolve_home_dir(home_dir)
    qmd_install_prefix = strongclaw_qmd_install_dir(home_dir=resolved_home)
    qmd_dist_entry = (
        qmd_install_prefix / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"
    )
    qmd_version_marker = qmd_install_prefix / ".strongclaw-qmd-version"
    expected_wrapper = resolved_home / ".bun" / "bin" / "qmd"
    if (
        expected_wrapper.is_file()
        and os.access(expected_wrapper, os.X_OK)
        and qmd_version_marker.exists()
        and qmd_version_marker.read_text(encoding="utf-8").strip() == DEFAULT_QMD_VERSION
        and run_command([str(expected_wrapper), "status"], timeout_seconds=30).ok
    ):
        return expected_wrapper
    node_command = _resolve_node_command()
    if not command_exists("npm") or node_command is None:
        raise CommandError("npm and node are required before QMD can be installed")
    qmd_install_prefix.mkdir(parents=True, exist_ok=True)
    _stream_checked(
        [
            "npm",
            "install",
            "--prefix",
            str(qmd_install_prefix),
            "--no-fund",
            "--no-audit",
            DEFAULT_QMD_PACKAGE,
        ],
        timeout_seconds=1800,
    )
    if not qmd_dist_entry.exists():
        raise CommandError(f"QMD install finished but {qmd_dist_entry} is missing")
    expected_wrapper.parent.mkdir(parents=True, exist_ok=True)
    expected_wrapper.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" f'exec {node_command} "{qmd_dist_entry}" "$@"\n',
        encoding="utf-8",
    )
    expected_wrapper.chmod(0o755)
    qmd_version_marker.write_text(DEFAULT_QMD_VERSION + "\n", encoding="utf-8")
    if not run_command([str(expected_wrapper), "status"], timeout_seconds=30).ok:
        raise CommandError(
            f"QMD install finished but {expected_wrapper} did not pass the health check"
        )
    return expected_wrapper


def install_memory_plugin_asset(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Install the vendored memory plugin dependencies."""
    plugin_dir = resolve_managed_plugin_dir("memory-lancedb-pro", home_dir=home_dir)
    plugin_source = resolve_asset_path("platform/plugins/memory-lancedb-pro", repo_root=repo_root)
    mirror_asset_tree(plugin_source, plugin_dir, ignore_names=("node_modules",))
    if not command_exists("npm"):
        raise CommandError("npm is required")
    _stream_checked(["npm", "ci"], cwd=plugin_dir, timeout_seconds=1800)
    host = platform.system().lower()
    arch = platform.machine().lower()
    lancedb_version = resolve_memory_plugin_lancedb_version(
        detect_host_platform(os_name=host, architecture=arch)
    )
    # NOTE: keep the host-compat LanceDB override behavior from the shell surface.
    if lancedb_version != "0.26.2":
        _stream_checked(
            [
                "npm",
                "install",
                "--no-fund",
                "--no-audit",
                "--no-save",
                f"@lancedb/lancedb@{lancedb_version}",
            ],
            cwd=plugin_dir,
            timeout_seconds=1800,
        )
    return plugin_dir


def install_lossless_claw_asset(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Install the lossless-claw plugin checkout."""
    lossless_dir = strongclaw_lossless_claw_dir(home_dir=home_dir)
    ref_marker = lossless_dir / ".strongclaw-lossless-ref"
    plugin_manifest = lossless_dir / "openclaw.plugin.json"
    package_manifest = lossless_dir / "package.json"
    if (
        plugin_manifest.exists()
        and package_manifest.exists()
        and (lossless_dir / "node_modules").is_dir()
        and ref_marker.exists()
        and ref_marker.read_text(encoding="utf-8").strip() == DEFAULT_LOSSLESS_CLAW_REF
        and run_command(
            ["npm", "--prefix", str(lossless_dir), "ls", "--omit=dev"], timeout_seconds=60
        ).ok
    ):
        return lossless_dir
    if not command_exists("git") or not command_exists("npm"):
        raise CommandError("git and npm are required to install lossless-claw")
    if (lossless_dir / ".git").is_dir():
        _stream_checked(
            [
                "git",
                "-C",
                str(lossless_dir),
                "fetch",
                "--depth=1",
                "origin",
                DEFAULT_LOSSLESS_CLAW_REF,
            ],
            timeout_seconds=1800,
        )
        _stream_checked(
            ["git", "-C", str(lossless_dir), "checkout", "--force", "FETCH_HEAD"],
            timeout_seconds=1800,
        )
    elif lossless_dir.exists():
        if not (plugin_manifest.exists() and package_manifest.exists()):
            raise CommandError(
                f"{lossless_dir} exists but is not a lossless-claw checkout. Move it aside or configure STRONGCLAW_LOSSLESS_CLAW_DIR."
            )
    else:
        lossless_dir.parent.mkdir(parents=True, exist_ok=True)
        _stream_checked(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                DEFAULT_LOSSLESS_CLAW_REF,
                DEFAULT_LOSSLESS_CLAW_REPO,
                str(lossless_dir),
            ],
            timeout_seconds=1800,
        )
    _stream_checked(
        ["npm", "ci", "--omit=dev", "--no-fund", "--no-audit"],
        cwd=lossless_dir,
        timeout_seconds=1800,
    )
    ref_marker.write_text(DEFAULT_LOSSLESS_CLAW_REF + "\n", encoding="utf-8")
    return lossless_dir


def install_profile_assets(
    repo_root: pathlib.Path,
    *,
    profile: str,
    home_dir: pathlib.Path | None = None,
) -> list[str]:
    """Install the assets required by *profile*."""
    installed_assets: list[str] = []
    if profile_requires_qmd(profile):
        install_qmd_asset(home_dir=home_dir)
        installed_assets.append("qmd")
    if profile_requires_memory_pro_plugin(profile):
        install_memory_plugin_asset(repo_root, home_dir=home_dir)
        installed_assets.append("memory-lancedb-pro")
    if profile_requires_lossless_claw(profile):
        install_lossless_claw_asset(repo_root, home_dir=home_dir)
        installed_assets.append("lossless-claw")
    return installed_assets


def uv_sync_managed_environment(
    repo_root: pathlib.Path,
    *,
    home_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Run `uv sync` for the managed StrongClaw environment."""
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir)
    if layout.source_checkout_root is None:
        return pathlib.Path(sys.executable).resolve()
    uv_binary = ensure_uv_installed(home_dir=home_dir)
    command = [
        str(uv_binary),
        "sync",
        "--project",
        str(layout.source_checkout_root),
        "--python",
        "3.12",
        "--locked",
    ]
    for attempt in range(1, _UV_SYNC_MAX_ATTEMPTS + 1):
        try:
            _stream_checked(command, timeout_seconds=3600)
            return uv_binary
        except CommandError:
            if attempt == _UV_SYNC_MAX_ATTEMPTS:
                raise
            delay_seconds = _UV_SYNC_RETRY_DELAY_SECONDS * attempt
            print(
                "uv sync failed during bootstrap; "
                f"retrying in {delay_seconds}s (attempt {attempt + 1}/{_UV_SYNC_MAX_ATTEMPTS})",
                flush=True,
            )
            time.sleep(delay_seconds)
    raise AssertionError("unreachable")


def _render_post_bootstrap_config(
    repo_root: pathlib.Path,
    *,
    profile: str,
    home_dir: pathlib.Path,
) -> None:
    """Render the default OpenClaw config after the venv exists."""
    command = managed_clawops_command(
        repo_root,
        "render-openclaw-config",
        "--asset-root",
        str(repo_root),
        "--home-dir",
        str(home_dir),
        "--profile",
        profile,
    )
    _stream_checked(command, cwd=repo_root, timeout_seconds=1800)


def _run_post_bootstrap_doctor(repo_root: pathlib.Path) -> None:
    """Run the host doctor using the project venv."""
    _stream_checked(
        managed_clawops_command(repo_root, "doctor-host"), cwd=repo_root, timeout_seconds=1800
    )


def bootstrap_host(
    repo_root: pathlib.Path,
    *,
    profile: str,
    home_dir: pathlib.Path,
) -> dict[str, object]:
    """Run the two-phase host bootstrap."""
    normalized_host_os = platform.system()
    if normalized_host_os == "Darwin":
        if not command_exists("brew"):
            raise CommandError("Homebrew is required on macOS")
        _ensure_command_or_brew("sqlite3", "sqlite")
        _ensure_python_runtime_darwin()
        _ensure_node_runtime_darwin()
    elif normalized_host_os == "Linux":
        if (
            not command_exists("sudo")
            or not command_exists("apt-get")
            or not command_exists("curl")
        ):
            raise CommandError("Linux bootstrap requires sudo, apt-get, and curl")
        _stream_checked(
            [
                "sudo",
                "apt-get",
                "update",
            ],
            timeout_seconds=3600,
        )
        _stream_checked(
            [
                "sudo",
                "apt-get",
                "install",
                "-y",
                "python3",
                "python3-pip",
                "sqlite3",
                "curl",
                "unzip",
                "ca-certificates",
                "gnupg",
            ],
            timeout_seconds=3600,
        )
        _ensure_node_runtime_linux()
    else:
        raise CommandError(f"unsupported host OS for bootstrap: {normalized_host_os}")

    docker_installed_by_bootstrap = ensure_docker_compatible_runtime(normalized_host_os)
    ensure_varlock_installed(DEFAULT_VARLOCK_VERSION)
    uv_sync_managed_environment(repo_root, home_dir=home_dir)
    install_profile_assets(repo_root, profile=profile, home_dir=home_dir)

    npm_install_command = [
        "npm",
        "install",
        "-g",
        f"openclaw@{DEFAULT_OPENCLAW_VERSION}",
        f"acpx@{DEFAULT_ACPX_VERSION}",
    ]
    if normalized_host_os == "Linux":
        npm_install_command.insert(0, "sudo")
    _stream_checked(npm_install_command, timeout_seconds=3600)

    ensure_common_state_roots(home_dir=home_dir)
    _render_post_bootstrap_config(repo_root, profile=profile, home_dir=home_dir)
    _run_post_bootstrap_doctor(repo_root)

    runtime_user = resolve_runtime_user(repo_root)
    if normalized_host_os == "Linux" and docker_installed_by_bootstrap:
        repair_linux_runtime_user_docker_access(runtime_user)

    mark_bootstrap_complete(
        profile=profile,
        host_os=normalized_host_os,
        runtime_user=runtime_user,
    )
    return {
        "ok": True,
        "profile": profile,
        "hostOs": normalized_host_os,
        "runtimeUser": runtime_user,
        "dockerInstalledByBootstrap": docker_installed_by_bootstrap,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for host bootstrap."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_asset_root_argument(parser)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    parser.add_argument("--profile", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for StrongClaw host bootstrap."""
    args = parse_args(argv)
    repo_root = resolve_asset_root_argument(args, command_name="clawops bootstrap")
    home_dir = resolve_home_dir(args.home_dir)
    profile = resolve_profile(args.profile)
    payload = bootstrap_host(repo_root, profile=profile, home_dir=home_dir)
    print(json.dumps(payload, sort_keys=True))
    return 0
