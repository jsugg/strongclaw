"""Linux disk preflight so compose image pulls fail fast instead of filling the runner disk.

Required capacity is estimated from the missing-image inventory using measured unpacked
sizes, plus pull-transient and scenario headroom. When the Docker root filesystem is
short, only an audited allowlist of expendable hosted-runner toolchains is removed, one
path at a time, until the requirement is met; otherwise the phase fails `insufficient_disk`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from tests.utils.helpers._fresh_host.models import FreshHostError
from tests.utils.helpers._hosted_docker.io import log, now_iso, write_json
from tests.utils.helpers._hosted_docker.shell import run_checked

INSUFFICIENT_DISK: Final[str] = "insufficient_disk"
# Unpacked sizes measured from hosted Linux `docker images` diagnostics (nightly 2026-11-10),
# rounded up; refresh from `diagnostics/docker-images.jsonl` when a compose image changes size.
MEASURED_UNPACKED_IMAGE_BYTES: Final[Mapping[str, int]] = {
    "ghcr.io/berriai/litellm": 6_000_000_000,
    "mcr.microsoft.com/playwright": 3_000_000_000,
    "neo4j": 1_100_000_000,
    "postgres": 450_000_000,
    "otel/opentelemetry-collector-contrib": 600_000_000,
    "ubuntu/squid": 350_000_000,
    "qdrant/qdrant": 300_000_000,
}
UNKNOWN_IMAGE_BYTES: Final[int] = 2_000_000_000
# Compressed blobs coexist with their extracted layers while parallel pulls unpack.
PULL_TRANSIENT_MARGIN_BYTES: Final[int] = 2_000_000_000
# Containers, volumes, logs, and package installs the scenario writes after pulling.
SCENARIO_HEADROOM_BYTES: Final[int] = 3_000_000_000
# Preinstalled hosted-runner toolchains this workflow never uses, largest first.
EXPENDABLE_RUNNER_PATHS: Final[tuple[Path, ...]] = (
    Path("/usr/local/lib/android"),
    Path("/usr/local/.ghcup"),
    Path("/opt/ghc"),
    Path("/usr/share/dotnet"),
    Path("/usr/share/swift"),
    Path("/opt/hostedtoolcache/CodeQL"),
    Path("/usr/local/share/powershell"),
    Path("/usr/local/share/boost"),
)
DEFAULT_DOCKER_ROOT: Final[Path] = Path("/var/lib/docker")


@dataclass(frozen=True, slots=True)
class DiskUsage:
    """Capacity of one filesystem, in bytes."""

    total: int
    used: int
    free: int


type DiskProbe = Callable[[Path], DiskUsage]
type PathRemover = Callable[[Path], None]
type SameDevice = Callable[[Path, Path], bool]


@dataclass(slots=True)
class DiskPreflightReport:
    """Evidence for one disk preflight decision."""

    docker_root: str
    required_bytes: int
    estimate_by_image: dict[str, int]
    free_before_bytes: int
    free_after_bytes: int
    reclaimed_bytes: int
    removed_paths: list[str]
    skipped_paths: list[str]
    sufficient: bool
    created_at: str


def probe_disk(path: Path) -> DiskUsage:
    """Measure the filesystem that holds *path*."""
    usage = shutil.disk_usage(path)
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


def remove_with_sudo(path: Path, *, timeout_seconds: float = 600) -> None:
    """Remove one allowlisted runner path; hosted runners grant passwordless sudo."""
    if path not in EXPENDABLE_RUNNER_PATHS:
        raise FreshHostError(f"refusing to remove non-allowlisted path {path}")
    run_checked(
        ["sudo", "-n", "rm", "-rf", "--", str(path)],
        cwd=Path("/"),
        env=dict(os.environ),
        timeout_seconds=timeout_seconds,
    )


def same_device(candidate: Path, anchor: Path) -> bool:
    """Return whether removing *candidate* can free space on *anchor*'s filesystem."""
    try:
        return os.stat(candidate).st_dev == os.stat(anchor).st_dev
    except OSError:
        return False


def image_repository(reference: str) -> str:
    """Return the repository of an image reference, without digest, tag, or `docker.io/`."""
    name = reference.split("@", 1)[0]
    last_segment = name.rsplit("/", 1)[-1]
    if ":" in last_segment:
        name = name[: len(name) - len(last_segment)] + last_segment.split(":", 1)[0]
    for prefix in ("docker.io/library/", "docker.io/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def estimate_required_bytes(missing_images: Sequence[str]) -> tuple[int, dict[str, int]]:
    """Estimate the free space pulling *missing_images* and running the scenario needs."""
    by_image = {
        image: MEASURED_UNPACKED_IMAGE_BYTES.get(image_repository(image), UNKNOWN_IMAGE_BYTES)
        for image in missing_images
    }
    if not by_image:
        return 0, by_image
    required = sum(by_image.values()) + PULL_TRANSIENT_MARGIN_BYTES + SCENARIO_HEADROOM_BYTES
    return required, by_image


def docker_root_dir(*, cwd: Path, env: Mapping[str, str], timeout_seconds: float = 60) -> Path:
    """Return the daemon's Docker root directory, defaulting to the standard Linux path."""
    try:
        completed = run_checked(
            ["docker", "info", "--format", "{{.DockerRootDir}}"],
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            timeout_seconds=timeout_seconds,
        )
    except (FreshHostError, subprocess.TimeoutExpired):
        return DEFAULT_DOCKER_ROOT
    root = completed.stdout.strip()
    return Path(root) if root else DEFAULT_DOCKER_ROOT


def ensure_pull_capacity(
    *,
    docker_root: Path,
    missing_images: Sequence[str],
    report_path: Path,
    probe: DiskProbe = probe_disk,
    remover: PathRemover = remove_with_sudo,
    device_check: SameDevice = same_device,
    path_exists: Callable[[Path], bool] = Path.exists,
    candidates: Sequence[Path] = EXPENDABLE_RUNNER_PATHS,
) -> DiskPreflightReport:
    """Reclaim allowlisted space when needed; raise `insufficient_disk` when still short."""
    unknown = [path for path in candidates if path not in EXPENDABLE_RUNNER_PATHS]
    if unknown:
        raise FreshHostError(f"disk preflight candidates outside the allowlist: {unknown}")
    required, by_image = estimate_required_bytes(missing_images)
    before = probe(docker_root)
    free = before.free
    removed: list[str] = []
    skipped: list[str] = []
    if free < required:
        log(
            f"Docker root {docker_root} has {free / 1e9:.1f}GB free; pulls need "
            f"{required / 1e9:.1f}GB. Reclaiming allowlisted runner toolchains."
        )
        for path in candidates:
            if not path_exists(path) or not device_check(path, docker_root):
                skipped.append(str(path))
                continue
            remover(path)
            removed.append(str(path))
            free = probe(docker_root).free
            if free >= required:
                break
    report = DiskPreflightReport(
        docker_root=str(docker_root),
        required_bytes=required,
        estimate_by_image=by_image,
        free_before_bytes=before.free,
        free_after_bytes=free,
        reclaimed_bytes=max(0, free - before.free),
        removed_paths=removed,
        skipped_paths=skipped,
        sufficient=free >= required,
        created_at=now_iso(),
    )
    write_json(asdict(report), report_path)
    if not report.sufficient:
        raise FreshHostError(
            f"{INSUFFICIENT_DISK}: {free / 1e9:.1f}GB free on {docker_root} after reclaiming "
            f"{report.reclaimed_bytes / 1e9:.1f}GB; image pulls need {required / 1e9:.1f}GB"
        )
    return report
