"""Hosted macOS runtime installation helpers."""

from __future__ import annotations

import os
import tarfile
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._fresh_host.storage import load_context, load_report, write_report
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_github_env, write_json
from tests.utils.helpers._hosted_docker.models import RuntimeInstallReport
from tests.utils.helpers._hosted_docker.shell import (
    macos_env,
    run_checked,
    run_command,
    sysctl_int,
    wait_for_docker_ready,
)


def download_to_cache(
    url: str,
    output_path: Path,
    *,
    label: str,
    cwd: Path,
    env: dict[str, str],
) -> None:
    """Download one runtime payload into the cache when it is missing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file():
        log(f"Using cached {label}: {output_path}")
        return
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    run_checked(
        [
            "curl",
            "-fL",
            "--retry",
            "3",
            "--retry-delay",
            "2",
            "--retry-connrefused",
            url,
            "-o",
            str(temporary_path),
        ],
        cwd=cwd,
        env=env,
        timeout_seconds=600,
    )
    temporary_path.replace(output_path)
    log(f"Downloaded {label} into runtime cache: {output_path}")


def install_runtime(
    context_path: Path, *, github_env_file: Path | None = None
) -> RuntimeInstallReport:
    """Install and start the hosted macOS Docker runtime."""
    context = load_context(context_path)
    if context.platform != "macos":
        raise FreshHostError(
            "hosted docker runtime installation is only supported for macOS contexts"
        )

    repo_root = Path(context.repo_root).resolve()
    report_path = Path(context.runtime_report_path or "").resolve()
    runtime_provider = context.runtime_provider or "colima"
    arch = os.uname().machine
    host_cpu_count = sysctl_int("hw.ncpu")
    host_memory_bytes = sysctl_int("hw.memsize")
    host_memory_gib = (
        max(1, host_memory_bytes // 1073741824) if host_memory_bytes is not None else None
    )
    colima_cpu_count = host_cpu_count or 2
    colima_memory_gib = min(10, max(6, (host_memory_gib or 9) - 3))
    runtime_cache_root = os.environ.get("FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR", "").strip()
    runtime_cache_dir = (
        Path(runtime_cache_root).expanduser().resolve() / arch if runtime_cache_root else None
    )
    lima_version = os.environ.get("MACOS_LIMA_VERSION", "").strip()
    colima_version = os.environ.get("MACOS_COLIMA_VERSION", "").strip()
    docker_config = str((Path.home() / ".docker").resolve())
    docker_host = f"unix://{Path.home() / '.colima' / 'default' / 'docker.sock'}"
    env = macos_env()
    env["DOCKER_CONFIG"] = docker_config
    env["DOCKER_HOST"] = docker_host
    failure_reason: str | None = None
    started_at = now_iso()
    started = time.monotonic()

    try:
        if arch != "x86_64":
            raise FreshHostError(
                "GitHub-hosted arm64 macOS runners do not support nested virtualization; use macos-15-intel."
            )
        if runtime_provider != "colima":
            raise FreshHostError(
                f"Unsupported hosted macOS runtime provider: {runtime_provider}. Hosted CI uses colima."
            )
        if runtime_cache_dir is None or not lima_version or not colima_version:
            raise FreshHostError(
                "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR, MACOS_LIMA_VERSION, and MACOS_COLIMA_VERSION must be configured"
            )
        run_checked(
            ["sudo", "mkdir", "-p", "/usr/local/libexec"],
            cwd=repo_root,
            env=env,
            timeout_seconds=120,
        )
        run_checked(
            ["sudo", "chown", f"{os.getuid()}:{os.getgid()}", "/usr/local/libexec"],
            cwd=repo_root,
            env=env,
            timeout_seconds=120,
        )
        runtime_cache_dir.mkdir(parents=True, exist_ok=True)
        log(
            "Runtime provider="
            f"{runtime_provider} cache-dir={runtime_cache_dir} host-cpu={colima_cpu_count} "
            f"host-memory-gib={host_memory_gib or 9}"
        )
        lima_archive_path = runtime_cache_dir / f"lima-{lima_version}-{arch}.tar.gz"
        colima_binary_path = runtime_cache_dir / f"colima-{colima_version}-{arch}"
        download_to_cache(
            f"https://github.com/lima-vm/lima/releases/download/{lima_version}/lima-{lima_version.removeprefix('v')}-Darwin-{arch}.tar.gz",
            lima_archive_path,
            label="Lima payload",
            cwd=repo_root,
            env=env,
        )
        download_to_cache(
            f"https://github.com/abiosoft/colima/releases/download/{colima_version}/colima-Darwin-{arch}",
            colima_binary_path,
            label="Colima binary",
            cwd=repo_root,
            env=env,
        )
        colima_binary_path.chmod(0o755)
        with tempfile.TemporaryDirectory() as temporary_root:
            with tarfile.open(lima_archive_path, mode="r:gz") as archive:
                archive.extractall(path=temporary_root, filter="data")
            run_checked(
                ["sudo", "rsync", "-a", f"{temporary_root}/", "/usr/local/"],
                cwd=repo_root,
                env=env,
                timeout_seconds=600,
            )
        run_checked(
            ["sudo", "install", "-m", "0755", str(colima_binary_path), "/usr/local/bin/colima"],
            cwd=repo_root,
            env=env,
            timeout_seconds=120,
        )
        run_checked(["brew", "install", "docker", "docker-compose"], cwd=repo_root, env=env)
        docker_plugin_dir = Path.home() / ".docker" / "cli-plugins"
        docker_plugin_dir.mkdir(parents=True, exist_ok=True)
        compose_prefix = run_checked(
            ["brew", "--prefix", "docker-compose"],
            cwd=repo_root,
            env=env,
            capture_output=True,
        ).stdout.strip()
        plugin_link = docker_plugin_dir / "docker-compose"
        if plugin_link.exists() or plugin_link.is_symlink():
            plugin_link.unlink()
        plugin_link.symlink_to(Path(compose_prefix) / "bin" / "docker-compose")
        run_checked(
            [
                "colima",
                "start",
                "--cpu",
                str(colima_cpu_count),
                "--memory",
                str(colima_memory_gib),
                "--disk",
                os.environ.get("MACOS_COLIMA_DISK_GIB", "20"),
                "--arch",
                "x86_64",
                "--vm-type",
                "vz",
                "--mount-type",
                "virtiofs",
            ],
            cwd=repo_root,
            env=env,
            timeout_seconds=1800,
        )
        wait_for_docker_ready(cwd=repo_root, env=env)
        for command in (
            ["docker", "version"],
            ["docker", "compose", "version"],
            ["docker", "info"],
        ):
            run_checked(command, cwd=repo_root, env=env, timeout_seconds=120)
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)
    finished_at = now_iso()
    duration_seconds = round(time.monotonic() - started, 3)

    report = RuntimeInstallReport(
        runtime_provider=runtime_provider,
        arch=arch,
        host_cpu_count=host_cpu_count,
        host_memory_gib=host_memory_gib,
        colima_cpu_count=colima_cpu_count,
        colima_memory_gib=colima_memory_gib,
        docker_host=docker_host,
        docker_config=docker_config,
        installed_tools=["lima", "colima", "docker", "docker-compose"],
        failure_reason=failure_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        created_at=finished_at,
    )
    write_json(asdict(report), report_path)
    main_report = load_report(Path(context.report_path).resolve())
    main_report.runtime_provider = runtime_provider
    if failure_reason is not None:
        main_report.failure_reason = failure_reason
        main_report.status = "failure"
    write_report(main_report, Path(context.report_path).resolve())
    write_github_env(
        {
            "DOCKER_CONFIG": docker_config,
            "DOCKER_HOST": docker_host,
            "FRESH_HOST_HOST_CPU_COUNT": str(host_cpu_count or ""),
            "FRESH_HOST_HOST_MEMORY_GIB": str(host_memory_gib or ""),
            "FRESH_HOST_COLIMA_CPU_COUNT": str(colima_cpu_count),
            "FRESH_HOST_COLIMA_MEMORY_GIB": str(colima_memory_gib),
        },
        github_env_file,
    )
    if failure_reason is not None:
        raise FreshHostError(failure_reason)
    return report


def install_runtime_tools(context_path: Path, *, github_env_file: Path | None = None) -> None:
    """Download and install Lima and Colima binaries for the hosted macOS runtime.

    This function performs the binary-only portion of runtime setup — downloading and
    installing Lima and the Colima binary — then writes the computed runtime parameters
    to *github_env_file*.  It intentionally does NOT install Docker client tooling
    (brew) or start the Colima VM.

    Callers should immediately start the Colima VM in a background shell step so that
    Docker tooling installation (brew) overlaps with VM initialization, then call
    :func:`wait_runtime_ready` to block until the Docker socket is responsive.
    """
    context = load_context(context_path)
    if context.platform != "macos":
        raise FreshHostError(
            "hosted docker runtime installation is only supported for macOS contexts"
        )

    repo_root = Path(context.repo_root).resolve()
    runtime_provider = context.runtime_provider or "colima"
    arch = os.uname().machine
    host_cpu_count = sysctl_int("hw.ncpu")
    host_memory_bytes = sysctl_int("hw.memsize")
    host_memory_gib = (
        max(1, host_memory_bytes // 1073741824) if host_memory_bytes is not None else None
    )
    colima_cpu_count = host_cpu_count or 2
    colima_memory_gib = min(10, max(6, (host_memory_gib or 9) - 3))
    runtime_cache_root = os.environ.get("FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR", "").strip()
    runtime_cache_dir = (
        Path(runtime_cache_root).expanduser().resolve() / arch if runtime_cache_root else None
    )
    lima_version = os.environ.get("MACOS_LIMA_VERSION", "").strip()
    colima_version = os.environ.get("MACOS_COLIMA_VERSION", "").strip()
    docker_config = str((Path.home() / ".docker").resolve())
    docker_host = f"unix://{Path.home() / '.colima' / 'default' / 'docker.sock'}"
    env = macos_env()
    env["DOCKER_CONFIG"] = docker_config
    env["DOCKER_HOST"] = docker_host

    if arch != "x86_64":
        raise FreshHostError(
            "GitHub-hosted arm64 macOS runners do not support nested virtualization;"
            " use macos-15-intel."
        )
    if runtime_provider != "colima":
        raise FreshHostError(
            f"Unsupported hosted macOS runtime provider: {runtime_provider}."
            " Hosted CI uses colima."
        )
    if runtime_cache_dir is None or not lima_version or not colima_version:
        raise FreshHostError(
            "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR, MACOS_LIMA_VERSION,"
            " and MACOS_COLIMA_VERSION must be configured"
        )

    run_checked(
        ["sudo", "mkdir", "-p", "/usr/local/libexec"],
        cwd=repo_root,
        env=env,
        timeout_seconds=120,
    )
    run_checked(
        ["sudo", "chown", f"{os.getuid()}:{os.getgid()}", "/usr/local/libexec"],
        cwd=repo_root,
        env=env,
        timeout_seconds=120,
    )
    runtime_cache_dir.mkdir(parents=True, exist_ok=True)
    log(
        f"Runtime provider={runtime_provider} cache-dir={runtime_cache_dir}"
        f" host-cpu={colima_cpu_count} host-memory-gib={host_memory_gib or 9}"
    )

    lima_archive_path = runtime_cache_dir / f"lima-{lima_version}-{arch}.tar.gz"
    colima_binary_path = runtime_cache_dir / f"colima-{colima_version}-{arch}"
    download_to_cache(
        f"https://github.com/lima-vm/lima/releases/download/{lima_version}"
        f"/lima-{lima_version.removeprefix('v')}-Darwin-{arch}.tar.gz",
        lima_archive_path,
        label="Lima payload",
        cwd=repo_root,
        env=env,
    )
    download_to_cache(
        f"https://github.com/abiosoft/colima/releases/download/{colima_version}"
        f"/colima-Darwin-{arch}",
        colima_binary_path,
        label="Colima binary",
        cwd=repo_root,
        env=env,
    )
    colima_binary_path.chmod(0o755)
    with tempfile.TemporaryDirectory() as temporary_root:
        with tarfile.open(lima_archive_path, mode="r:gz") as archive:
            archive.extractall(path=temporary_root, filter="data")
        run_checked(
            ["sudo", "rsync", "-a", f"{temporary_root}/", "/usr/local/"],
            cwd=repo_root,
            env=env,
            timeout_seconds=600,
        )
    run_checked(
        ["sudo", "install", "-m", "0755", str(colima_binary_path), "/usr/local/bin/colima"],
        cwd=repo_root,
        env=env,
        timeout_seconds=120,
    )
    write_github_env(
        {
            "DOCKER_CONFIG": docker_config,
            "DOCKER_HOST": docker_host,
            "FRESH_HOST_HOST_CPU_COUNT": str(host_cpu_count or ""),
            "FRESH_HOST_HOST_MEMORY_GIB": str(host_memory_gib or ""),
            "FRESH_HOST_COLIMA_CPU_COUNT": str(colima_cpu_count),
            "FRESH_HOST_COLIMA_MEMORY_GIB": str(colima_memory_gib),
        },
        github_env_file,
    )
    log(
        f"Runtime tools installed. Colima params: cpu={colima_cpu_count}"
        f" memory={colima_memory_gib}GiB."
        " Start Colima in the background, then call wait-runtime-ready."
    )


def _log_colima_startup_diagnostics(repo_root: Path, env: dict[str, str]) -> None:
    """Emit best-effort Colima/Docker diagnostics to stdout for CI log visibility."""
    log("=== Colima startup diagnostics ===")
    colima_log = Path("/tmp/colima-start.log")
    if colima_log.exists():
        log(f"--- /tmp/colima-start.log ({colima_log.stat().st_size} bytes) ---")
        try:
            log(colima_log.read_text(encoding="utf-8", errors="replace")[-4000:])
        except OSError as exc:
            log(f"(could not read colima-start.log: {exc})")
    else:
        log("/tmp/colima-start.log not found — nohup may not have created it.")
    for label, command in [
        ("colima status", ["colima", "status"]),
        ("colima list", ["colima", "list"]),
        ("ps colima", ["ps", "aux"]),
    ]:
        try:
            completed = run_command(
                command, cwd=repo_root, env=env, timeout_seconds=30, capture_output=True
            )
            stdout: str = completed.stdout or ""
            stderr: str = completed.stderr or ""
            output: str = stdout + stderr
            if label == "ps colima":
                output = "\n".join(line for line in output.splitlines() if "colima" in line.lower())
            log(f"--- {label} ---\n{output.strip()[:2000]}")
        except Exception as exc:  # noqa: BLE001
            log(f"--- {label} failed: {exc} ---")
    log("=== end Colima diagnostics ===")


def wait_runtime_ready(context_path: Path) -> RuntimeInstallReport:
    """Wait for the Colima Docker runtime to become ready and write the install report.

    Call this after the Colima VM has been started in a background shell step.  The
    function polls until the Docker socket responds, runs health-check commands, and
    writes the :class:`RuntimeInstallReport` for the scenario.
    """
    context = load_context(context_path)
    if context.platform != "macos":
        raise FreshHostError(
            "hosted docker runtime installation is only supported for macOS contexts"
        )

    repo_root = Path(context.repo_root).resolve()
    report_path = Path(context.runtime_report_path or "").resolve()
    runtime_provider = context.runtime_provider or "colima"
    arch = os.uname().machine
    docker_config = os.environ.get("DOCKER_CONFIG", str((Path.home() / ".docker").resolve()))
    docker_host = os.environ.get(
        "DOCKER_HOST",
        f"unix://{Path.home() / '.colima' / 'default' / 'docker.sock'}",
    )
    env = macos_env()
    env["DOCKER_CONFIG"] = docker_config
    env["DOCKER_HOST"] = docker_host

    def _env_int(key: str) -> int | None:
        raw = os.environ.get(key, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    host_cpu_count = _env_int("FRESH_HOST_HOST_CPU_COUNT")
    host_memory_gib = _env_int("FRESH_HOST_HOST_MEMORY_GIB")
    colima_cpu_count = _env_int("FRESH_HOST_COLIMA_CPU_COUNT") or 2
    colima_memory_gib = _env_int("FRESH_HOST_COLIMA_MEMORY_GIB") or 6

    failure_reason: str | None = None
    started_at = now_iso()
    started = time.monotonic()

    log(f"wait_runtime_ready: DOCKER_HOST={docker_host} DOCKER_CONFIG={docker_config}")
    socket_path = docker_host.removeprefix("unix://")
    log(
        f"wait_runtime_ready: socket {socket_path}: "
        f"{'EXISTS' if os.path.exists(socket_path) else 'NOT FOUND'}"
    )

    try:
        wait_for_docker_ready(cwd=repo_root, env=env)
        for command in (
            ["docker", "version"],
            ["docker", "compose", "version"],
            ["docker", "info"],
        ):
            run_checked(command, cwd=repo_root, env=env, timeout_seconds=120)
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)
        _log_colima_startup_diagnostics(repo_root, env)

    finished_at = now_iso()
    duration_seconds = round(time.monotonic() - started, 3)

    report = RuntimeInstallReport(
        runtime_provider=runtime_provider,
        arch=arch,
        host_cpu_count=host_cpu_count,
        host_memory_gib=host_memory_gib,
        colima_cpu_count=colima_cpu_count,
        colima_memory_gib=colima_memory_gib,
        docker_host=docker_host,
        docker_config=docker_config,
        installed_tools=["lima", "colima", "docker", "docker-compose"],
        failure_reason=failure_reason,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        created_at=finished_at,
    )
    write_json(asdict(report), report_path)
    main_report = load_report(Path(context.report_path).resolve())
    main_report.runtime_provider = runtime_provider
    if failure_reason is not None:
        main_report.failure_reason = failure_reason
        main_report.status = "failure"
    write_report(main_report, Path(context.report_path).resolve())
    if failure_reason is not None:
        raise FreshHostError(failure_reason)
    return report
