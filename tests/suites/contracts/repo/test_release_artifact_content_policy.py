"""Contract checks for release artifact content and size policy."""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path

from tests.utils.helpers.repo import REPO_ROOT

MAX_RELEASE_ARTIFACT_SIZE_BYTES = 12_000_000
FORBIDDEN_ARTIFACT_PATH_MARKERS: tuple[str, ...] = ("clawops/assets/platform/compose/state/",)


def _archive_paths(artifact_path: Path) -> list[str]:
    """Return archive member paths from one wheel or source distribution."""
    if artifact_path.suffix == ".whl":
        with zipfile.ZipFile(artifact_path) as archive:
            return archive.namelist()
    if artifact_path.name.endswith(".tar.gz"):
        with tarfile.open(artifact_path, "r:gz") as archive:
            return [member.name for member in archive.getmembers()]
    return []


def test_release_artifacts_enforce_size_and_forbidden_content_policy(tmp_path: Path) -> None:
    """Built wheel and sdist must not ship runtime state and must fit size budget."""
    dist_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "run", "--locked", "python", "-m", "build", "--outdir", str(dist_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    artifacts = sorted(path for path in dist_dir.iterdir() if path.is_file())
    assert any(path.suffix == ".whl" for path in artifacts)
    assert any(path.name.endswith(".tar.gz") for path in artifacts)

    for artifact_path in artifacts:
        assert artifact_path.stat().st_size <= MAX_RELEASE_ARTIFACT_SIZE_BYTES
        for archive_path in _archive_paths(artifact_path):
            normalized_archive_path = archive_path.replace("\\", "/")
            for marker in FORBIDDEN_ARTIFACT_PATH_MARKERS:
                assert marker not in normalized_archive_path
