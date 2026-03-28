"""Helpers for release workflow scripting."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked


def clean_artifact_directories(paths: list[Path]) -> None:
    """Delete build output directories before a new release build."""
    for path in paths:
        shutil.rmtree(path.expanduser().resolve(), ignore_errors=True)


def verify_release_artifacts(dist_dir: Path) -> None:
    """Verify built release artifacts and install them into fresh virtualenvs."""
    resolved_dist_dir = dist_dir.expanduser().resolve()
    artifacts = sorted(path for path in resolved_dist_dir.iterdir() if path.is_file())
    if not artifacts:
        raise CiWorkflowError(f"no release artifacts found in {resolved_dist_dir}")
    wheel_path = next((path for path in artifacts if path.suffix == ".whl"), None)
    sdist_path = next((path for path in artifacts if path.name.endswith(".tar.gz")), None)
    if wheel_path is None:
        raise CiWorkflowError(f"missing wheel artifact in {resolved_dist_dir}")
    if sdist_path is None:
        raise CiWorkflowError(f"missing source distribution in {resolved_dist_dir}")

    run_checked(["uv", "run", "twine", "check", *[str(path) for path in artifacts]])
    with tempfile.TemporaryDirectory(prefix="strongclaw-release-verify.") as tmp_dir:
        tmp_root = Path(tmp_dir)
        _install_and_smoke_test(tmp_root / "wheel-env", wheel_path)
        _install_and_smoke_test(tmp_root / "sdist-env", sdist_path)


def publish_github_release(tag: str, dist_dir: Path, sbom_path: Path) -> None:
    """Create or update the GitHub release for *tag*."""
    resolved_dist_dir = dist_dir.expanduser().resolve()
    resolved_sbom_path = sbom_path.expanduser().resolve()
    assets = [str(path) for path in sorted(resolved_dist_dir.iterdir()) if path.is_file()]
    if not assets:
        raise CiWorkflowError(f"no release assets found in {resolved_dist_dir}")
    if not resolved_sbom_path.is_file():
        raise CiWorkflowError(f"missing SBOM at {resolved_sbom_path}")
    assets.append(str(resolved_sbom_path))

    try:
        run_checked(["gh", "release", "view", tag], capture_output=True)
    except CiWorkflowError:
        run_checked(["gh", "release", "create", tag, *assets, "--verify-tag", "--generate-notes"])
        return
    run_checked(["gh", "release", "upload", tag, *assets, "--clobber"])


def _install_and_smoke_test(venv_dir: Path, artifact_path: Path) -> None:
    """Install an artifact into a fresh virtualenv and assert importability."""
    run_checked([sys.executable, "-m", "venv", str(venv_dir)])
    venv_python = venv_dir / "bin" / "python"
    run_checked([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
    run_checked([str(venv_python), "-m", "pip", "install", str(artifact_path)])
    run_checked(
        [
            str(venv_python),
            "-c",
            "import importlib.metadata as metadata; import clawops; "
            "assert metadata.version('clawops'); assert clawops.__file__",
        ]
    )
