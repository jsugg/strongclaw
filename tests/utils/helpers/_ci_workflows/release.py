"""Helpers for release workflow scripting."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked
from tests.utils.helpers._ci_workflows.required_checks import load_required_check_manifest

RELEASE_MANIFEST_NAME = "strongclaw-release-manifest.json"
RELEASE_CHECKSUMS_NAME = "SHA256SUMS"
MAX_RELEASE_ARTIFACT_SIZE_BYTES = 12_000_000
FORBIDDEN_ARTIFACT_PATH_MARKERS: tuple[str, ...] = ("clawops/assets/platform/compose/state/",)
PLATFORM_WHEEL_PREFIX = "clawops/assets/platform/"
IMAGE_LINE_RE = re.compile(r"^\s*image:\s*(?P<reference>[^#\s]+)", flags=re.MULTILINE)
IMAGE_DIGEST_RE = re.compile(r"@(?P<digest>sha256:[0-9a-f]{64})$")
REQUIRED_RUNTIME_ASSET_PATHS: tuple[str, ...] = (
    "docs/SECURITY_MODEL.md",
    "docs/CI_AND_SECURITY.md",
    "configs/openclaw/30-channels.json5",
    "configs/openclaw/00-baseline.json5",
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
GITHUB_REPOSITORY = "jsugg/strongclaw"
RELEASE_REQUIRED_CHECK_NAME = "Verdict"
RELEASE_MAIN_REF = "refs/remotes/origin/main"


@dataclass(frozen=True)
class PlatformAssetSnapshot:
    """Digest inventory for the packaged runtime platform assets."""

    files: list[dict[str, object]]
    container_images: list[dict[str, object]]
    file_count: int
    total_size_bytes: int
    tree_sha256: str


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
    wheel_path, sdist_path = _required_distribution_artifacts(resolved_dist_dir)

    for artifact_path in artifacts:
        _enforce_artifact_content_policy(artifact_path)

    run_checked(["uv", "run", "twine", "check", str(wheel_path), str(sdist_path)])
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

    package_metadata = _read_package_metadata(repo_root.expanduser().resolve())
    expected_tag = f"v{package_metadata['version']}"
    if normalized_tag != expected_tag:
        raise CiWorkflowError(
            f"release tag/version mismatch: got {normalized_tag!r}, expected {expected_tag!r}"
        )


def verify_release_tag_preflight(
    *,
    tag: str,
    repo_root: Path,
    repository: str | None = None,
) -> None:
    """Assert a release tag is version-correct, on main, and CI-green."""
    normalized_tag = tag.strip()
    resolved_root = repo_root.expanduser().resolve()
    verify_tag_version_parity(tag=normalized_tag, repo_root=resolved_root)

    tag_commit = _git_stdout(
        ["git", "rev-parse", f"{normalized_tag}^{{commit}}"],
        cwd=resolved_root,
    )
    run_checked(
        ["git", "fetch", "--no-tags", "origin", f"+main:{RELEASE_MAIN_REF}"],
        cwd=resolved_root,
    )
    try:
        run_checked(
            ["git", "merge-base", "--is-ancestor", tag_commit, RELEASE_MAIN_REF],
            cwd=resolved_root,
        )
    except CiWorkflowError as exc:
        raise CiWorkflowError(
            f"release tag {normalized_tag} points at {tag_commit}, "
            f"which is not reachable from origin/main"
        ) from exc

    repository_name = (
        repository or os.environ.get("GITHUB_REPOSITORY", "").strip() or GITHUB_REPOSITORY
    )
    required_checks = load_required_check_manifest(resolved_root / ".github/required-checks.json")
    matching_contexts = tuple(
        context
        for context in required_checks.contexts
        if context.context == RELEASE_REQUIRED_CHECK_NAME
    )
    if len(matching_contexts) != 1 or matching_contexts[0].app_id is None:
        raise CiWorkflowError(
            f"required-check manifest must define one app-bound {RELEASE_REQUIRED_CHECK_NAME} context"
        )
    if repository_name != required_checks.repository:
        raise CiWorkflowError(
            f"release repository {repository_name!r} does not match required-check manifest "
            f"{required_checks.repository!r}"
        )
    _require_successful_check_run(
        repository=repository_name,
        commit_sha=tag_commit,
        check_name=RELEASE_REQUIRED_CHECK_NAME,
        app_id=matching_contexts[0].app_id,
    )


def write_release_metadata(
    *,
    tag: str,
    repo_root: Path,
    dist_dir: Path,
    sbom_path: Path,
) -> tuple[Path, Path]:
    """Write release manifest and checksum files from built artifacts."""
    normalized_tag = tag.strip()
    verify_tag_version_parity(tag=normalized_tag, repo_root=repo_root)
    resolved_root = repo_root.expanduser().resolve()
    resolved_dist_dir = dist_dir.expanduser().resolve()
    resolved_sbom_path = sbom_path.expanduser().resolve()
    if not resolved_sbom_path.is_file():
        raise CiWorkflowError(f"missing SBOM at {resolved_sbom_path}")

    manifest_path = resolved_dist_dir / RELEASE_MANIFEST_NAME
    checksums_path = resolved_dist_dir / RELEASE_CHECKSUMS_NAME
    manifest_path.unlink(missing_ok=True)
    checksums_path.unlink(missing_ok=True)

    wheel_path, sdist_path = _required_distribution_artifacts(resolved_dist_dir)
    package_metadata = _read_package_metadata(resolved_root)
    platform_snapshot = _platform_asset_snapshot_from_wheel(wheel_path)
    unpinned_images = [
        str(image["reference"])
        for image in platform_snapshot.container_images
        if image.get("digest") is None
    ]
    if unpinned_images:
        examples = ", ".join(unpinned_images[:5])
        raise CiWorkflowError(f"release manifest requires digest-pinned images: {examples}")

    release_assets = [wheel_path, sdist_path, resolved_sbom_path, manifest_path, checksums_path]
    _assert_unique_release_asset_names(release_assets)
    manifest_payload = _release_manifest_payload(
        tag=normalized_tag,
        package_metadata=package_metadata,
        wheel_path=wheel_path,
        sdist_path=sdist_path,
        sbom_path=resolved_sbom_path,
        platform_snapshot=platform_snapshot,
    )
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    checksum_targets = _dedupe_paths([wheel_path, sdist_path, resolved_sbom_path, manifest_path])
    checksums_path.write_text(_checksum_lines(checksum_targets), encoding="utf-8")
    return manifest_path, checksums_path


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
    for metadata_name in (RELEASE_MANIFEST_NAME, RELEASE_CHECKSUMS_NAME):
        metadata_path = resolved_dist_dir / metadata_name
        if not metadata_path.is_file():
            raise CiWorkflowError(f"missing release metadata asset: {metadata_path}")
    assets = [str(path) for path in sorted(resolved_dist_dir.iterdir()) if path.is_file()]
    if not assets:
        raise CiWorkflowError(f"no release assets found in {resolved_dist_dir}")
    if not resolved_sbom_path.is_file():
        raise CiWorkflowError(f"missing SBOM at {resolved_sbom_path}")
    if resolved_sbom_path not in {path.resolve() for path in resolved_dist_dir.iterdir()}:
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


def _required_distribution_artifacts(dist_dir: Path) -> tuple[Path, Path]:
    """Return the required wheel and source distribution from *dist_dir*."""
    artifacts = sorted(path for path in dist_dir.iterdir() if path.is_file())
    wheel_path = next((path for path in artifacts if path.suffix == ".whl"), None)
    sdist_path = next((path for path in artifacts if path.name.endswith(".tar.gz")), None)
    if wheel_path is None:
        raise CiWorkflowError(f"missing wheel artifact in {dist_dir}")
    if sdist_path is None:
        raise CiWorkflowError(f"missing source distribution in {dist_dir}")
    return wheel_path, sdist_path


def _git_stdout(command: list[str], *, cwd: Path) -> str:
    """Run a git command and return stripped stdout."""
    result = run_checked(command, cwd=cwd, capture_output=True)
    stdout = result.stdout.strip()
    if not stdout:
        raise CiWorkflowError(f"command produced empty stdout: {' '.join(command)}")
    return stdout


def _require_successful_check_run(
    *,
    repository: str,
    commit_sha: str,
    check_name: str,
    app_id: int,
) -> None:
    """Require a successful check run from the expected GitHub App."""
    normalized_repository = repository.strip()
    if "/" not in normalized_repository:
        raise CiWorkflowError(f"invalid GitHub repository name: {repository!r}")
    result = run_checked(
        [
            "gh",
            "api",
            f"repos/{normalized_repository}/commits/{commit_sha}/check-runs?check_name={check_name}",
            "--jq",
            f'[.check_runs[] | select(.name == "{check_name}" and '
            f'.conclusion == "success" and .app.id == {app_id})] | length',
        ],
        capture_output=True,
    )
    raw_count = result.stdout.strip()
    try:
        successful_checks = int(raw_count)
    except ValueError as exc:
        raise CiWorkflowError(
            f"could not parse {check_name} check-run count for {commit_sha}: {raw_count!r}"
        ) from exc
    if successful_checks < 1:
        raise CiWorkflowError(
            f"release commit {commit_sha} does not have a successful {check_name} check run "
            f"from app {app_id}"
        )


def _read_package_metadata(repo_root: Path) -> dict[str, str]:
    """Read and validate package metadata from project files."""
    pyproject_path = repo_root / "pyproject.toml"
    package_init_path = repo_root / "src" / "clawops" / "__init__.py"
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
    project_name = project_mapping.get("name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise CiWorkflowError("pyproject.toml [project].name must be a non-empty string")
    pyproject_version = project_mapping.get("version")
    if not isinstance(pyproject_version, str) or not pyproject_version.strip():
        raise CiWorkflowError("pyproject.toml [project].version must be a non-empty string")
    requires_python = project_mapping.get("requires-python", "")
    if not isinstance(requires_python, str):
        raise CiWorkflowError("pyproject.toml [project].requires-python must be a string")

    init_text = package_init_path.read_text(encoding="utf-8")
    match = re.search(r"^__version__\s*=\s*\"([^\"]+)\"\s*$", init_text, flags=re.MULTILINE)
    if match is None:
        raise CiWorkflowError(f"could not parse __version__ from {package_init_path}")
    package_version = match.group(1)
    normalized_pyproject_version = pyproject_version.strip()
    if normalized_pyproject_version != package_version:
        raise CiWorkflowError(
            "version mismatch between pyproject.toml and src/clawops/__init__.py: "
            f"{normalized_pyproject_version!r} != {package_version!r}"
        )
    return {
        "name": project_name.strip(),
        "version": normalized_pyproject_version,
        "requiresPython": requires_python.strip(),
    }


def _release_manifest_payload(
    *,
    tag: str,
    package_metadata: dict[str, str],
    wheel_path: Path,
    sdist_path: Path,
    sbom_path: Path,
    platform_snapshot: PlatformAssetSnapshot,
) -> dict[str, object]:
    """Build the JSON payload for the release manifest."""
    repository = os.environ.get("GITHUB_REPOSITORY", GITHUB_REPOSITORY).strip() or GITHUB_REPOSITORY
    return {
        "manifestVersion": 1,
        "generatedAt": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "release": {
            "tag": tag,
            "repository": repository,
            "authority": "github-release-assets",
        },
        "package": package_metadata,
        "installAuthority": {
            "primaryArtifact": wheel_path.name,
            "fallbackArtifact": sdist_path.name,
            "trustEvidence": [sbom_path.name, RELEASE_CHECKSUMS_NAME, "github-attestations"],
        },
        "artifacts": [
            _release_asset_entry(wheel_path, role="python-wheel", authority="primary"),
            _release_asset_entry(sdist_path, role="source-distribution", authority="fallback"),
            _release_asset_entry(sbom_path, role="spdx-sbom", authority="trust-evidence"),
        ],
        "runtimeAssets": {
            "sourceArtifact": wheel_path.name,
            "root": PLATFORM_WHEEL_PREFIX.rstrip("/"),
            "fileCount": platform_snapshot.file_count,
            "totalSizeBytes": platform_snapshot.total_size_bytes,
            "treeSha256": platform_snapshot.tree_sha256,
            "files": platform_snapshot.files,
        },
        "containerImages": platform_snapshot.container_images,
        "migration": {
            "notes": [],
            "statePolicy": "Release artifacts exclude platform/compose/state; install/update must not replace user runtime state.",
        },
        "install": {
            "commands": [
                f"python -m pip install ./{wheel_path.name}",
                "python -m clawops setup",
            ],
        },
        "update": {
            "commands": [
                f"python -m pip install --upgrade ./{wheel_path.name}",
                "python -m clawops setup",
            ],
        },
        "verification": {
            "commands": [
                f"sha256sum --check {RELEASE_CHECKSUMS_NAME}",
                f"gh attestation verify ./{wheel_path.name} --repo {repository}",
                "python -m clawops --help",
                "python -m clawops render-openclaw-config --profile hypermemory --output /tmp/strongclaw-openclaw.json",
            ],
        },
    }


def _release_asset_entry(path: Path, *, role: str, authority: str) -> dict[str, object]:
    """Return a manifest entry for one release asset."""
    return {
        "name": path.name,
        "role": role,
        "authority": authority,
        "sizeBytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _platform_asset_snapshot_from_wheel(wheel_path: Path) -> PlatformAssetSnapshot:
    """Build the runtime platform asset inventory from the release wheel."""
    file_entries: list[dict[str, object]] = []
    image_entries: list[dict[str, object]] = []
    digest_lines: list[str] = []
    total_size_bytes = 0
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            for info in sorted(archive.infolist(), key=lambda member: member.filename):
                if info.is_dir() or not info.filename.startswith(PLATFORM_WHEEL_PREFIX):
                    continue
                relative_path = info.filename.removeprefix(PLATFORM_WHEEL_PREFIX)
                if not relative_path:
                    continue
                content = archive.read(info)
                file_sha256 = hashlib.sha256(content).hexdigest()
                size_bytes = len(content)
                file_entries.append(
                    {
                        "path": relative_path,
                        "sizeBytes": size_bytes,
                        "sha256": file_sha256,
                    }
                )
                digest_lines.append(f"{file_sha256}  {size_bytes}  {relative_path}")
                total_size_bytes += size_bytes
                image_entries.extend(_container_image_entries(relative_path, content))
    except (OSError, zipfile.BadZipFile) as exc:
        raise CiWorkflowError(f"failed to inspect release wheel {wheel_path}: {exc}") from exc

    if not file_entries:
        raise CiWorkflowError(f"wheel {wheel_path} does not contain packaged platform assets")
    tree_payload = "\n".join(digest_lines) + "\n"
    return PlatformAssetSnapshot(
        files=file_entries,
        container_images=image_entries,
        file_count=len(file_entries),
        total_size_bytes=total_size_bytes,
        tree_sha256=hashlib.sha256(tree_payload.encode("utf-8")).hexdigest(),
    )


def _container_image_entries(relative_path: str, content: bytes) -> list[dict[str, object]]:
    """Return container image references found in one packaged asset."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []

    entries: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = IMAGE_LINE_RE.match(line)
        if match is None:
            continue
        reference = match.group("reference")
        digest_match = IMAGE_DIGEST_RE.search(reference)
        digest = digest_match.group("digest") if digest_match is not None else None
        entries.append(
            {
                "path": relative_path,
                "line": line_number,
                "reference": reference,
                "digest": digest,
                "digestPinned": digest is not None,
            }
        )
    return entries


def _assert_unique_release_asset_names(paths: list[Path]) -> None:
    """Fail if release asset basenames would collide on GitHub Releases."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for path in paths:
        if path.name in seen:
            duplicates.add(path.name)
        seen.add(path.name)
    if duplicates:
        raise CiWorkflowError(
            "release assets must have unique filenames: " + ", ".join(sorted(duplicates))
        )


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Return paths with duplicate resolved paths removed while preserving first use."""
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    return deduped


def _checksum_lines(paths: list[Path]) -> str:
    """Return SHA256SUMS content for release assets."""
    lines = [
        f"{_sha256_file(path)}  {path.name}" for path in sorted(paths, key=lambda item: item.name)
    ]
    return "\n".join(lines) + "\n"


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
