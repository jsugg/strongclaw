"""Hosted Docker runtime helpers for fresh-host CI."""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tests.utils.helpers.fresh_host import FreshHostError, load_context, load_report, write_report

LOG_PREFIX: Final[str] = "[hosted-docker]"
DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS: Final[int] = 1800
PULL_HEARTBEAT_SECONDS: Final[int] = 30


@dataclass(slots=True)
class PullReport:
    """Structured report describing one image pull sequence."""

    exit_code: int
    pulled_images: list[str]
    failed_images: list[str]
    attempt_count: int
    retried_images: list[str]


@dataclass(slots=True)
class RuntimeInstallReport:
    """Structured report describing runtime installation."""

    runtime_provider: str
    arch: str
    host_cpu_count: int | None
    host_memory_gib: int | None
    colima_cpu_count: int | None
    colima_memory_gib: int | None
    docker_host: str | None
    docker_config: str | None
    installed_tools: list[str]
    failure_reason: str | None
    created_at: str


@dataclass(slots=True)
class ImageEnsureReport:
    """Structured report describing image ensure status."""

    compose_files: list[str]
    images: list[str]
    local_before: list[str]
    missing_before_pull: list[str]
    pulled_images: list[str]
    missing_after_pull: list[str]
    pull_parallelism: int
    pull_attempt_count: int
    retried_images: list[str]
    failure_reason: str | None
    created_at: str


def _log(message: str) -> None:
    """Emit one CI-friendly log line."""
    print(f"{LOG_PREFIX} {message}", flush=True)


def _now_iso() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC).isoformat()


def _write_json(payload: object, path: Path) -> None:
    """Persist one JSON payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_github_env(assignments: dict[str, str], github_env_file: Path | None) -> None:
    """Append one batch of exports to GITHUB_ENV."""
    if github_env_file is None:
        return
    with github_env_file.open("a", encoding="utf-8") as handle:
        for key, value in assignments.items():
            handle.write(f"{key}={value}\n")


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 3600,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command with optional captured output."""
    _log("Running: " + " ".join(command))
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        check=False,
        capture_output=capture_output,
        text=True,
        timeout=timeout_seconds,
    )


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int = 3600,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command and raise on non-zero exit."""
    completed = _run_command(
        command,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        capture_output=capture_output,
    )
    if completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise FreshHostError(f"{' '.join(command)} failed: {output}")
    return completed


def _sysctl_int(name: str) -> int | None:
    """Return one integer sysctl value when available."""
    try:
        completed = subprocess.run(
            ["sysctl", "-n", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    raw_value = completed.stdout.strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def _macos_env() -> dict[str, str]:
    """Return the base environment for hosted macOS tooling."""
    env = dict(os.environ)
    env.setdefault("HOMEBREW_NO_AUTO_UPDATE", "1")
    env.setdefault("HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK", "1")
    env.setdefault("HOMEBREW_NO_INSTALL_CLEANUP", "1")
    env.setdefault("HOMEBREW_NO_INSTALL_UPGRADE", "1")
    return env


def _download_to_cache(
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
        _log(f"Using cached {label}: {output_path}")
        return
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    _run_checked(
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
    _log(f"Downloaded {label} into runtime cache: {output_path}")


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
    host_cpu_count = _sysctl_int("hw.ncpu")
    host_memory_bytes = _sysctl_int("hw.memsize")
    host_memory_gib = (
        max(1, host_memory_bytes // 1073741824) if host_memory_bytes is not None else None
    )
    effective_host_cpu_count = host_cpu_count or 2
    effective_host_memory_gib = host_memory_gib or 9
    colima_cpu_count = effective_host_cpu_count
    colima_memory_gib = min(10, max(6, effective_host_memory_gib - 3))
    colima_disk_gib = int(os.environ.get("MACOS_COLIMA_DISK_GIB", "20"))
    runtime_cache_root = os.environ.get("FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR", "").strip()
    runtime_cache_dir = (
        Path(runtime_cache_root).expanduser().resolve() / arch if runtime_cache_root else None
    )
    lima_version = os.environ.get("MACOS_LIMA_VERSION", "").strip()
    colima_version = os.environ.get("MACOS_COLIMA_VERSION", "").strip()
    docker_config = str((Path.home() / ".docker").resolve())
    docker_host = f"unix://{Path.home() / '.colima' / 'default' / 'docker.sock'}"
    env = _macos_env()
    env["DOCKER_CONFIG"] = docker_config
    env["DOCKER_HOST"] = docker_host
    failure_reason: str | None = None

    try:
        if arch != "x86_64":
            raise FreshHostError(
                "GitHub-hosted arm64 macOS runners do not support nested virtualization; use macos-15-intel."
            )
        if runtime_provider != "colima":
            raise FreshHostError(
                f"Unsupported hosted macOS runtime provider: {runtime_provider}. Hosted CI uses colima."
            )
        if not lima_version or not colima_version:
            raise FreshHostError("MACOS_LIMA_VERSION and MACOS_COLIMA_VERSION must be configured")
        _run_checked(
            ["sudo", "mkdir", "-p", "/usr/local/libexec"],
            cwd=repo_root,
            env=env,
            timeout_seconds=120,
        )
        _run_checked(
            ["sudo", "chown", f"{os.getuid()}:{os.getgid()}", "/usr/local/libexec"],
            cwd=repo_root,
            env=env,
            timeout_seconds=120,
        )
        if runtime_cache_dir is None:
            raise FreshHostError(
                "FRESH_HOST_MACOS_RUNTIME_DOWNLOAD_CACHE_DIR must be configured for hosted macOS"
            )
        runtime_cache_dir.mkdir(parents=True, exist_ok=True)
        _log(
            "Runtime provider="
            f"{runtime_provider} cache-dir={runtime_cache_dir} host-cpu={effective_host_cpu_count} "
            f"host-memory-gib={effective_host_memory_gib}"
        )
        lima_archive_path = runtime_cache_dir / f"lima-{lima_version}-{arch}.tar.gz"
        colima_binary_path = runtime_cache_dir / f"colima-{colima_version}-{arch}"
        _download_to_cache(
            (
                f"https://github.com/lima-vm/lima/releases/download/{lima_version}/"
                f"lima-{lima_version.removeprefix('v')}-Darwin-{arch}.tar.gz"
            ),
            lima_archive_path,
            label="Lima payload",
            cwd=repo_root,
            env=env,
        )
        _download_to_cache(
            (
                f"https://github.com/abiosoft/colima/releases/download/{colima_version}/"
                f"colima-Darwin-{arch}"
            ),
            colima_binary_path,
            label="Colima binary",
            cwd=repo_root,
            env=env,
        )
        colima_binary_path.chmod(0o755)
        with tempfile.TemporaryDirectory() as temporary_root:
            with tarfile.open(lima_archive_path, mode="r:gz") as archive:
                archive.extractall(path=temporary_root, filter="data")
            _run_checked(
                ["sudo", "rsync", "-a", f"{temporary_root}/", "/usr/local/"],
                cwd=repo_root,
                env=env,
                timeout_seconds=600,
            )
        _run_checked(
            ["sudo", "install", "-m", "0755", str(colima_binary_path), "/usr/local/bin/colima"],
            cwd=repo_root,
            env=env,
            timeout_seconds=120,
        )
        _run_checked(["brew", "install", "docker", "docker-compose"], cwd=repo_root, env=env)
        docker_plugin_dir = Path.home() / ".docker" / "cli-plugins"
        docker_plugin_dir.mkdir(parents=True, exist_ok=True)
        compose_prefix = _run_checked(
            ["brew", "--prefix", "docker-compose"],
            cwd=repo_root,
            env=env,
            capture_output=True,
        ).stdout.strip()
        compose_plugin_target = Path(compose_prefix) / "bin" / "docker-compose"
        plugin_link = docker_plugin_dir / "docker-compose"
        if plugin_link.exists() or plugin_link.is_symlink():
            plugin_link.unlink()
        plugin_link.symlink_to(compose_plugin_target)

        _run_checked(
            [
                "colima",
                "start",
                "--cpu",
                str(colima_cpu_count),
                "--memory",
                str(colima_memory_gib),
                "--disk",
                str(colima_disk_gib),
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
        for _ in range(60):
            ready = _run_command(
                ["docker", "info"],
                cwd=repo_root,
                env=env,
                timeout_seconds=30,
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(2)
        else:
            raise FreshHostError("docker did not become ready after starting colima")

        _run_checked(["docker", "version"], cwd=repo_root, env=env)
        _run_checked(["docker", "compose", "version"], cwd=repo_root, env=env)
        _run_checked(["docker", "info"], cwd=repo_root, env=env)
    except Exception as exc:  # noqa: BLE001
        failure_reason = str(exc)

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
        created_at=_now_iso(),
    )
    _write_json(asdict(report), report_path)
    main_report = load_report(Path(context.report_path).resolve())
    main_report.runtime_provider = runtime_provider
    if failure_reason is not None:
        main_report.failure_reason = failure_reason
        main_report.status = "failure"
    write_report(main_report, Path(context.report_path).resolve())
    _write_github_env(
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


def resolve_compose_images(
    compose_files: Sequence[Path], *, cwd: Path, env: dict[str, str]
) -> list[str]:
    """Resolve compose image references in first-seen order."""
    images: list[str] = []
    seen: set[str] = set()
    for compose_file in compose_files:
        completed = _run_checked(
            ["docker", "compose", "-f", str(compose_file), "config", "--images"],
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout_seconds=120,
        )
        for raw_line in completed.stdout.splitlines():
            image = raw_line.strip()
            if not image or image in seen:
                continue
            seen.add(image)
            images.append(image)
    if not images:
        raise FreshHostError("No compose images were resolved.")
    return images


def list_local_images(images: Sequence[str]) -> list[str]:
    """Return the image refs already present in the local daemon."""
    present: list[str] = []
    for image in images:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            present.append(image)
    return present


def _pull_one_image(image: str, timeout_seconds: int) -> tuple[str, int, float, str]:
    """Pull one image and return status information."""
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["docker", "pull", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        duration_seconds = time.monotonic() - started
        return image, 1, duration_seconds, f"docker pull timed out after {timeout_seconds}s"
    duration_seconds = time.monotonic() - started
    output = "\n".join(chunk for chunk in (result.stdout.strip(), result.stderr.strip()) if chunk)
    return image, result.returncode, duration_seconds, output


def pull_images(
    images: Sequence[str],
    *,
    parallelism: int,
    max_attempts: int,
    pull_timeout_seconds: int = DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
) -> PullReport:
    """Pull images with bounded retries and reduced retry parallelism."""
    if parallelism < 1:
        raise FreshHostError("parallelism must be positive")
    if max_attempts < 1:
        raise FreshHostError("max_attempts must be positive")
    if pull_timeout_seconds < 1:
        raise FreshHostError("pull_timeout_seconds must be positive")

    outstanding = list(images)
    pulled_images: list[str] = []
    retried_images: list[str] = []
    seen_retries: set[str] = set()
    attempt_parallelism = parallelism
    attempt_count = 0
    while outstanding and attempt_count < max_attempts:
        attempt_count += 1
        _log(
            f"Pulling {len(outstanding)} image(s) with parallelism={attempt_parallelism} "
            f"(attempt {attempt_count}/{max_attempts})."
        )
        failures: list[str] = []
        worker_count = min(attempt_parallelism, len(outstanding))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_pull_one_image, image, pull_timeout_seconds): image
                for image in outstanding
            }
            pending = set(futures)
            while pending:
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=PULL_HEARTBEAT_SECONDS,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    active_images = ", ".join(sorted(futures[future] for future in pending))
                    _log(f"Waiting on {len(pending)} image pull(s): {active_images}")
                    continue
                for future in done:
                    image, returncode, duration_seconds, output = future.result()
                    duration = f"{duration_seconds:.1f}s"
                    if returncode == 0:
                        _log(f"[ok] {image} in {duration}")
                        pulled_images.append(image)
                        continue
                    _log(f"[failed] {image} in {duration}")
                    if output:
                        print(output, flush=True)
                    failures.append(image)
        outstanding = failures
        if not outstanding or attempt_count >= max_attempts:
            break
        for image in outstanding:
            if image in seen_retries:
                continue
            seen_retries.add(image)
            retried_images.append(image)
        next_parallelism = max(1, attempt_parallelism // 2)
        if next_parallelism != attempt_parallelism:
            _log(f"Reducing retry parallelism {attempt_parallelism}->{next_parallelism}.")
        attempt_parallelism = next_parallelism
        backoff_seconds = min(10, 2 * attempt_count)
        _log(f"Retrying {len(outstanding)} image(s) after {backoff_seconds}s.")
        time.sleep(backoff_seconds)

    if outstanding:
        return PullReport(
            exit_code=1,
            pulled_images=pulled_images,
            failed_images=outstanding,
            attempt_count=attempt_count,
            retried_images=retried_images,
        )
    return PullReport(
        exit_code=0,
        pulled_images=pulled_images,
        failed_images=[],
        attempt_count=attempt_count,
        retried_images=retried_images,
    )


def ensure_images(context_path: Path) -> ImageEnsureReport:
    """Ensure the scenario's compose images exist locally."""
    context = load_context(context_path)
    report_path = Path(context.image_report_path).resolve() if context.image_report_path else None
    repo_root = Path(context.repo_root).resolve()
    compose_files = [Path(path).resolve() for path in context.compose_files]
    compose_state_dir = Path(context.tmp_root).resolve() / "compose-prepull"
    compose_state_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["STRONGCLAW_COMPOSE_STATE_DIR"] = str(compose_state_dir)
    if context.compose_variant is not None:
        env["STRONGCLAW_COMPOSE_VARIANT"] = context.compose_variant

    if not context.ensure_images:
        report = ImageEnsureReport(
            compose_files=[str(path) for path in compose_files],
            images=[],
            local_before=[],
            missing_before_pull=[],
            pulled_images=[],
            missing_after_pull=[],
            pull_parallelism=context.docker_pull_parallelism,
            pull_attempt_count=0,
            retried_images=[],
            failure_reason=None,
            created_at=_now_iso(),
        )
        if report_path is not None:
            _write_json(asdict(report), report_path)
        return report

    images = resolve_compose_images(compose_files, cwd=repo_root, env=env)
    local_before = list_local_images(images)
    missing_before_pull = [image for image in images if image not in local_before]
    pulled_images: list[str] = []
    retried_images: list[str] = []
    attempt_count = 0
    failure_reason: str | None = None
    if missing_before_pull:
        pull_report = pull_images(
            missing_before_pull,
            parallelism=context.docker_pull_parallelism,
            max_attempts=context.docker_pull_max_attempts,
            pull_timeout_seconds=DEFAULT_DOCKER_PULL_TIMEOUT_SECONDS,
        )
        pulled_images = list(pull_report.pulled_images)
        retried_images = list(pull_report.retried_images)
        attempt_count = pull_report.attempt_count
        if pull_report.exit_code != 0:
            failure_reason = "docker pull failed"
    local_after_pull = list_local_images(images)
    missing_after_pull = [image for image in images if image not in local_after_pull]
    if missing_after_pull and failure_reason is None:
        failure_reason = "images remain unavailable after pull"

    report = ImageEnsureReport(
        compose_files=[str(path) for path in compose_files],
        images=images,
        local_before=local_before,
        missing_before_pull=missing_before_pull,
        pulled_images=pulled_images,
        missing_after_pull=missing_after_pull,
        pull_parallelism=context.docker_pull_parallelism,
        pull_attempt_count=attempt_count,
        retried_images=retried_images,
        failure_reason=failure_reason,
        created_at=_now_iso(),
    )
    if report_path is not None:
        _write_json(asdict(report), report_path)
    if failure_reason is not None:
        raise FreshHostError(failure_reason)
    return report


def collect_runtime_diagnostics(context_path: Path) -> None:
    """Collect best-effort runtime diagnostics for hosted macOS."""
    context = load_context(context_path)
    if context.platform != "macos":
        return
    diagnostics_dir = Path(context.diagnostics_dir).resolve()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(context.repo_root).resolve()
    env = dict(os.environ)
    commands = {
        diagnostics_dir / "runtime-status.txt": ["colima", "status"],
        diagnostics_dir / "runtime-list.txt": ["colima", "list"],
        diagnostics_dir / "docker-info.txt": ["docker", "info"],
        diagnostics_dir / "docker-system-df.txt": ["docker", "system", "df"],
        diagnostics_dir / "docker-images.jsonl": ["docker", "images", "--format", "{{json .}}"],
    }
    if context.compose_files:
        primary_compose_file = context.compose_files[0]
        commands[diagnostics_dir / "compose-ps.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "ps",
        ]
        commands[diagnostics_dir / "compose-logs.txt"] = [
            "docker",
            "compose",
            "-f",
            primary_compose_file,
            "logs",
            "--no-color",
        ]
    for output_path, command in commands.items():
        try:
            completed = _run_command(
                command,
                cwd=repo_root,
                env=env,
                timeout_seconds=120,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output_path.write_text(f"{exc}\n", encoding="utf-8")
            continue
        output_path.write_text(
            "\n".join(
                chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk
            )
            + "\n",
            encoding="utf-8",
        )
    extra_files = {
        diagnostics_dir / "host-cpu-count.txt": str(_sysctl_int("hw.ncpu") or ""),
        diagnostics_dir / "host-memory-bytes.txt": str(_sysctl_int("hw.memsize") or ""),
    }
    for output_path, content in extra_files.items():
        output_path.write_text(f"{content}\n", encoding="utf-8")
    cache_targets = {
        diagnostics_dir / "colima-disk-usage.txt": str(Path.home() / ".colima"),
        diagnostics_dir / "homebrew-cache-usage.txt": os.environ.get("HOMEBREW_CACHE", ""),
        diagnostics_dir / "workflow-cache-usage.txt": os.environ.get("FRESH_HOST_CACHE_ROOT", ""),
    }
    for output_path, raw_target_path in cache_targets.items():
        if not raw_target_path:
            continue
        target_path = Path(raw_target_path)
        try:
            completed = _run_command(
                ["du", "-sh", str(target_path)],
                cwd=repo_root,
                env=env,
                timeout_seconds=120,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output_path.write_text(f"{exc}\n", encoding="utf-8")
            continue
        output_path.write_text(
            "\n".join(
                chunk for chunk in (completed.stdout.strip(), completed.stderr.strip()) if chunk
            )
            + "\n",
            encoding="utf-8",
        )
    colima_logs_dir = Path.home() / ".colima" / "_lima" / "colima"
    if colima_logs_dir.is_dir():
        target_dir = diagnostics_dir / "colima-logs"
        target_dir.mkdir(parents=True, exist_ok=True)
        for log_path in colima_logs_dir.glob("*.log"):
            shutil.copyfile(log_path, target_dir / log_path.name)
