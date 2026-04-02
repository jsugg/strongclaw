"""Helpers for release workflow scripting."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import cast

from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked

MAX_RELEASE_ARTIFACT_SIZE_BYTES = 12_000_000
FORBIDDEN_ARTIFACT_PATH_MARKERS: tuple[str, ...] = ("clawops/assets/platform/compose/state/",)
REQUIRED_RUNTIME_ASSET_PATHS: tuple[str, ...] = (
    "docs/SECURITY_MODEL.md",
    "docs/CI_AND_SECURITY.md",
    "configs/openclaw/30-channels.json5",
    "configs/openclaw/00-defaults.json5",
)
RUNTIME_READINESS_CLAWOPS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("clawops", "doctor", "--asset-root", "."),
    ("clawops", "baseline", "verify", "--asset-root", "."),
    ("clawops", "verify-platform", "sidecars", "--asset-root", "."),
    ("clawops", "verify-platform", "observability", "--asset-root", "."),
    ("clawops", "verify-platform", "channels", "--asset-root", "."),
)
RUNTIME_READINESS_OPENCLAW_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("doctor",),
    ("security", "audit", "--deep"),
    ("secrets", "audit", "--check"),
)
LAUNCH_READINESS_CONTRACT_TEST_PATH = (
    "tests/suites/contracts/repo/launch_readiness/test_launch_readiness_audit_packet.py"
)


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

    for artifact_path in artifacts:
        _enforce_artifact_content_policy(artifact_path)

    run_checked(["uv", "run", "twine", "check", *[str(path) for path in artifacts]])
    with tempfile.TemporaryDirectory(prefix="strongclaw-release-verify.") as tmp_dir:
        tmp_root = Path(tmp_dir)
        _install_and_smoke_test(
            tmp_root / "wheel-env",
            wheel_path,
            smoke_workspace_root=tmp_root / "wheel-smoke",
        )
        _install_and_smoke_test(
            tmp_root / "sdist-env",
            sdist_path,
            smoke_workspace_root=tmp_root / "sdist-smoke",
        )


def verify_tag_version_parity(*, tag: str, repo_root: Path) -> None:
    """Assert that the release tag matches the Python package versions."""
    normalized_tag = tag.strip()
    if not normalized_tag.startswith("v"):
        raise CiWorkflowError(f"release tag must start with 'v', got {normalized_tag!r}")

    resolved_root = repo_root.expanduser().resolve()
    pyproject_path = resolved_root / "pyproject.toml"
    package_init_path = resolved_root / "src" / "clawops" / "__init__.py"
    if not pyproject_path.is_file():
        raise CiWorkflowError(f"missing pyproject file: {pyproject_path}")
    if not package_init_path.is_file():
        raise CiWorkflowError(f"missing package version file: {package_init_path}")

    pyproject_payload = cast(
        dict[str, object], tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    )
    project = pyproject_payload.get("project")
    if not isinstance(project, dict):
        raise CiWorkflowError("pyproject.toml must define [project]")
    project_mapping = cast(dict[str, object], project)
    pyproject_version = project_mapping.get("version")
    if not isinstance(pyproject_version, str) or not pyproject_version.strip():
        raise CiWorkflowError("pyproject.toml [project].version must be a non-empty string")
    normalized_pyproject_version = pyproject_version.strip()

    init_text = package_init_path.read_text(encoding="utf-8")
    match = re.search(r"^__version__\s*=\s*\"([^\"]+)\"\s*$", init_text, flags=re.MULTILINE)
    if match is None:
        raise CiWorkflowError(f"could not parse __version__ from {package_init_path}")
    package_version = match.group(1)

    if normalized_pyproject_version != package_version:
        raise CiWorkflowError(
            "version mismatch between pyproject.toml and src/clawops/__init__.py: "
            f"{normalized_pyproject_version!r} != {package_version!r}"
        )

    expected_tag = f"v{normalized_pyproject_version}"
    if normalized_tag != expected_tag:
        raise CiWorkflowError(
            f"release tag/version mismatch: got {normalized_tag!r}, expected {expected_tag!r}"
        )


def run_release_runtime_readiness(*, repo_root: Path) -> None:
    """Run launch-readiness commands required before release publishing."""
    resolved_root = repo_root.expanduser().resolve()
    for command_suffix in RUNTIME_READINESS_CLAWOPS_COMMANDS:
        run_checked([sys.executable, "-m", *command_suffix], cwd=resolved_root)

    openclaw_command = _resolve_openclaw_command()
    for command_suffix in RUNTIME_READINESS_OPENCLAW_COMMANDS:
        run_checked([*openclaw_command, *command_suffix], cwd=resolved_root)

    run_checked(
        [
            sys.executable,
            "./tests/scripts/security_workflow.py",
            "run-channels-runtime-smoke",
            "--repo-root",
            ".",
        ],
        cwd=resolved_root,
        env={
            **os.environ,
            "STRONGCLAW_CHANNELS_RUNTIME_TELEGRAM_BOT_TOKEN": "release-smoke-token",
        },
    )
    _run_live_launch_readiness_contract(repo_root=resolved_root)


def _run_live_launch_readiness_contract(*, repo_root: Path) -> None:
    """Generate a launch packet and validate it in live contract mode."""
    with tempfile.TemporaryDirectory(prefix="strongclaw-launch-readiness.") as tmp_dir:
        artifact_root = Path(tmp_dir) / "packet"
        run_checked(
            [
                sys.executable,
                "./tests/scripts/launch_readiness.py",
                "generate-audit-packet",
                "--output-dir",
                str(artifact_root),
            ],
            cwd=repo_root,
        )
        run_checked(
            [
                "uv",
                "run",
                "pytest",
                "-q",
                LAUNCH_READINESS_CONTRACT_TEST_PATH,
            ],
            cwd=repo_root,
            env={
                **os.environ,
                "STRONGCLAW_LAUNCH_READINESS_ARTIFACT_MODE": "live",
                "STRONGCLAW_LAUNCH_READINESS_ARTIFACT_ROOT": str(artifact_root),
            },
        )


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
        run_checked(
            [
                "gh",
                "release",
                "create",
                tag,
                *assets,
                "--verify-tag",
                "--generate-notes",
            ]
        )
        return
    run_checked(["gh", "release", "upload", tag, *assets, "--clobber"])


def _install_and_smoke_test(
    venv_dir: Path,
    artifact_path: Path,
    *,
    smoke_workspace_root: Path,
) -> None:
    """Install an artifact into a fresh virtualenv and assert import and CLI behavior."""
    run_checked([sys.executable, "-m", "venv", str(venv_dir)])
    venv_python = venv_dir / "bin" / "python"
    run_checked([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
    run_checked([str(venv_python), "-m", "pip", "install", str(artifact_path)])
    run_checked([str(venv_python), "-m", "clawops", "--help"])
    run_checked(
        [
            str(venv_python),
            "-c",
            "import importlib.metadata as metadata; import clawops; "
            "assert metadata.version('clawops'); assert clawops.__file__",
        ]
    )
    home_dir = smoke_workspace_root / "home"
    workspace_dir = smoke_workspace_root / "workspace"
    output_path = smoke_workspace_root / "openclaw.json"
    home_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            str(venv_python),
            "-m",
            "clawops",
            "render-openclaw-config",
            "--profile",
            "hypermemory",
            "--home-dir",
            str(home_dir),
            "--output",
            str(output_path),
        ],
        cwd=workspace_dir,
    )
    payload: object = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CiWorkflowError("render-openclaw-config produced non-object JSON output")
    if "plugins" not in payload:
        raise CiWorkflowError("render-openclaw-config output is missing plugins section")

    asset_root_result = run_checked(
        [
            str(venv_python),
            "-c",
            "import pathlib, clawops.assets; "
            "print((pathlib.Path(clawops.assets.__file__).resolve().parent / 'platform').as_posix())",
        ],
        capture_output=True,
    )
    asset_root = Path(asset_root_result.stdout.strip())
    for relative_path in REQUIRED_RUNTIME_ASSET_PATHS:
        candidate = asset_root / relative_path
        if not candidate.is_file():
            raise CiWorkflowError(f"installed runtime asset is missing: {candidate}")


def _enforce_artifact_content_policy(artifact_path: Path) -> None:
    """Fail when a release artifact violates content or size policy."""
    if artifact_path.stat().st_size > MAX_RELEASE_ARTIFACT_SIZE_BYTES:
        raise CiWorkflowError(
            f"artifact {artifact_path.name} exceeds max size "
            f"{MAX_RELEASE_ARTIFACT_SIZE_BYTES} bytes"
        )
    for archive_path in _archive_paths(artifact_path):
        normalized_archive_path = archive_path.replace("\\", "/")
        for marker in FORBIDDEN_ARTIFACT_PATH_MARKERS:
            if marker in normalized_archive_path:
                raise CiWorkflowError(
                    f"artifact {artifact_path.name} contains forbidden path "
                    f"{normalized_archive_path}"
                )


def enforce_artifact_content_policy(artifact_path: Path) -> None:
    """Public wrapper for release artifact content policy checks."""
    _enforce_artifact_content_policy(artifact_path)


def _archive_paths(artifact_path: Path) -> list[str]:
    """Return archive member paths from one wheel or source distribution."""
    if artifact_path.suffix == ".whl":
        with zipfile.ZipFile(artifact_path) as archive:
            return archive.namelist()
    if artifact_path.name.endswith(".tar.gz"):
        with tarfile.open(artifact_path, "r:gz") as archive:
            return [member.name for member in archive.getmembers()]
    return []


def _resolve_openclaw_command() -> list[str]:
    """Resolve the OpenClaw CLI invocation command for CI workflows."""
    openclaw_executable = shutil.which("openclaw")
    if openclaw_executable is not None:
        return [openclaw_executable]
    try:
        run_checked([sys.executable, "-m", "openclaw", "--help"], capture_output=True)
    except CiWorkflowError as exc:
        raise CiWorkflowError(
            "openclaw runtime-readiness checks require an installed OpenClaw CLI "
            "(binary or python module)"
        ) from exc
    return [sys.executable, "-m", "openclaw"]
