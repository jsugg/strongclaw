"""Unit coverage for the Linux disk preflight that runs before compose image pulls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers import fresh_host, hosted_docker
from tests.utils.helpers._hosted_docker import images as hosted_docker_images
from tests.utils.helpers._hosted_docker.disk import (
    EXPENDABLE_RUNNER_PATHS,
    PULL_TRANSIENT_MARGIN_BYTES,
    SCENARIO_HEADROOM_BYTES,
    UNKNOWN_IMAGE_BYTES,
    DiskUsage,
    ensure_pull_capacity,
    estimate_required_bytes,
    image_repository,
    remove_with_sudo,
)
from tests.utils.helpers._hosted_docker.events import read_events

_GB = 1_000_000_000
_LITELLM = "ghcr.io/berriai/litellm:main-stable@sha256:" + "a" * 64
_QDRANT = "qdrant/qdrant:v1.15.5@sha256:" + "b" * 64


class _FakeDisk:
    """Filesystem whose free space grows as allowlisted paths are removed."""

    def __init__(self, free: int, reclaim: dict[Path, int]) -> None:
        self.free = free
        self.reclaim = reclaim
        self.removed: list[Path] = []

    def probe(self, path: Path) -> DiskUsage:
        del path
        return DiskUsage(total=100 * _GB, used=100 * _GB - self.free, free=self.free)

    def remove(self, path: Path) -> None:
        self.removed.append(path)
        self.free += self.reclaim.get(path, 0)


def _same_device(candidate: Path, anchor: Path) -> bool:
    del candidate, anchor
    return True


def _present(path: Path) -> bool:
    del path
    return True


@pytest.mark.parametrize(
    ("reference", "repository"),
    [
        (_LITELLM, "ghcr.io/berriai/litellm"),
        ("postgres:16-alpine@sha256:" + "c" * 64, "postgres"),
        ("docker.io/library/postgres:16", "postgres"),
        ("docker.io/qdrant/qdrant:v1", "qdrant/qdrant"),
        ("registry.local:5000/team/app:1.2", "registry.local:5000/team/app"),
        ("registry.local:5000/team/app", "registry.local:5000/team/app"),
    ],
)
def test_image_repository_strips_tag_digest_and_default_registry(
    reference: str, repository: str
) -> None:
    assert image_repository(reference) == repository


def test_estimate_required_bytes_uses_measured_sizes_and_margins() -> None:
    required, by_image = estimate_required_bytes([_LITELLM, _QDRANT, "example/unknown:1"])

    assert by_image == {
        _LITELLM: 6 * _GB,
        _QDRANT: 300_000_000,
        "example/unknown:1": UNKNOWN_IMAGE_BYTES,
    }
    assert (
        required == sum(by_image.values()) + PULL_TRANSIENT_MARGIN_BYTES + SCENARIO_HEADROOM_BYTES
    )
    assert estimate_required_bytes([]) == (0, {})


def test_preflight_passes_without_cleanup_when_capacity_suffices(tmp_path: Path) -> None:
    disk = _FakeDisk(free=40 * _GB, reclaim={})

    report = ensure_pull_capacity(
        docker_root=tmp_path,
        missing_images=[_LITELLM],
        report_path=tmp_path / "disk.json",
        probe=disk.probe,
        remover=disk.remove,
        device_check=_same_device,
        path_exists=_present,
    )

    assert report.sufficient
    assert disk.removed == []
    assert json.loads((tmp_path / "disk.json").read_text(encoding="utf-8"))["sufficient"] is True


def test_preflight_reclaims_allowlisted_paths_only_until_enough(
    tmp_path: Path,
) -> None:
    android, ghcup, dotnet = (
        EXPENDABLE_RUNNER_PATHS[0],
        EXPENDABLE_RUNNER_PATHS[1],
        EXPENDABLE_RUNNER_PATHS[3],
    )
    disk = _FakeDisk(free=5 * _GB, reclaim={android: 4 * _GB, ghcup: 5 * _GB, dotnet: 3 * _GB})

    report = ensure_pull_capacity(
        docker_root=tmp_path,
        missing_images=[_LITELLM],
        report_path=tmp_path / "disk.json",
        probe=disk.probe,
        remover=disk.remove,
        device_check=_same_device,
        path_exists=_present,
    )

    # 6GB image + 5GB margins = 11GB; android then ghcup bring 5GB free up to 14GB.
    assert report.sufficient
    assert disk.removed == [android, ghcup]
    assert report.reclaimed_bytes == 9 * _GB


def test_preflight_skips_missing_and_other_filesystem_paths(
    tmp_path: Path,
) -> None:
    android, ghcup, ghc, dotnet = EXPENDABLE_RUNNER_PATHS[:4]
    disk = _FakeDisk(free=5 * _GB, reclaim={android: 1 * _GB, dotnet: 10 * _GB})

    report = ensure_pull_capacity(
        docker_root=tmp_path,
        missing_images=[_LITELLM],
        report_path=tmp_path / "disk.json",
        probe=disk.probe,
        remover=disk.remove,
        device_check=lambda candidate, anchor: candidate != ghcup,
        path_exists=lambda path: path != ghc,
    )

    assert report.sufficient
    assert disk.removed == [android, dotnet]
    assert report.skipped_paths == [str(ghcup), str(ghc)]


def test_preflight_fails_fast_with_insufficient_disk_after_cleanup(
    tmp_path: Path,
) -> None:
    disk = _FakeDisk(free=2 * _GB, reclaim={EXPENDABLE_RUNNER_PATHS[0]: 1 * _GB})

    with pytest.raises(fresh_host.FreshHostError, match="^insufficient_disk"):
        ensure_pull_capacity(
            docker_root=tmp_path,
            missing_images=[_LITELLM],
            report_path=tmp_path / "disk.json",
            probe=disk.probe,
            remover=disk.remove,
            device_check=_same_device,
            path_exists=_present,
        )

    payload = json.loads((tmp_path / "disk.json").read_text(encoding="utf-8"))
    assert payload["sufficient"] is False
    assert payload["removed_paths"] == [str(path) for path in EXPENDABLE_RUNNER_PATHS]


def test_preflight_rejects_candidates_outside_the_allowlist(tmp_path: Path) -> None:
    disk = _FakeDisk(free=1, reclaim={})

    with pytest.raises(fresh_host.FreshHostError, match="outside the allowlist"):
        ensure_pull_capacity(
            docker_root=tmp_path,
            missing_images=[_LITELLM],
            report_path=tmp_path / "disk.json",
            probe=disk.probe,
            remover=disk.remove,
            candidates=[Path("/home/runner/work")],
        )
    assert disk.removed == []


def test_remove_with_sudo_refuses_non_allowlisted_paths() -> None:
    with pytest.raises(fresh_host.FreshHostError, match="non-allowlisted"):
        remove_with_sudo(Path("/opt/hostedtoolcache/Python"))


def test_allowlist_never_covers_toolchains_or_workspace_the_lane_uses() -> None:
    protected = (
        Path("/opt/hostedtoolcache/Python"),
        Path("/opt/hostedtoolcache/node"),
        Path("/home/runner"),
        Path("/var/lib/docker"),
        Path("/usr/bin"),
        Path("/"),
    )
    for path in EXPENDABLE_RUNNER_PATHS:
        assert path.is_absolute()
        assert len(path.parts) >= 3, path
        for keep in protected:
            assert not keep.is_relative_to(path), (keep, path)
            assert not path.is_relative_to(keep) or keep in (Path("/"), Path("/home/runner"))


def test_ensure_images_fails_before_pulling_when_disk_is_insufficient(
    tmp_path: Path, test_context: TestContext
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_context.env.set("GITHUB_EVENT_NAME", "push")
    context = fresh_host.prepare_context(
        scenario_id="linux",
        repo_root=workspace,
        runner_temp=tmp_path / "runner-temp",
        workspace=workspace,
        github_env_file=tmp_path / "github.env",
    )

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int = 3600,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout_seconds, capture_output
        return subprocess.CompletedProcess(command, 0, stdout=f"{_LITELLM}\n")

    def no_space(**kwargs: object) -> object:
        del kwargs
        raise fresh_host.FreshHostError("insufficient_disk: 2.0GB free")

    def unexpected_pull(*args: object, **kwargs: object) -> hosted_docker.PullReport:
        raise AssertionError("pulls must not start without capacity")

    test_context.patch.patch_object(hosted_docker_images, "run_checked", new=fake_run_checked)

    def no_local_images(images: list[str]) -> list[str]:
        del images
        return []

    def fake_docker_root(*, cwd: Path, env: dict[str, str]) -> Path:
        del cwd, env
        return tmp_path

    test_context.patch.patch_object(hosted_docker_images, "list_local_images", new=no_local_images)
    test_context.patch.patch_object(hosted_docker_images, "docker_root_dir", new=fake_docker_root)
    test_context.patch.patch_object(hosted_docker_images, "ensure_pull_capacity", new=no_space)
    test_context.patch.patch_object(hosted_docker_images, "pull_images", new=unexpected_pull)

    with pytest.raises(fresh_host.FreshHostError, match="insufficient_disk"):
        hosted_docker.ensure_images(Path(context.context_path), environ={})

    assert context.image_report_path is not None
    payload = json.loads(Path(context.image_report_path).read_text(encoding="utf-8"))
    assert payload["failure_class"] == "insufficient_disk"
    events = read_events(Path(str(payload["pull_events_path"])))
    assert [event["event"] for event in events] == ["phase_started", "disk_preflight"]
    assert events[1]["sufficient"] is False
