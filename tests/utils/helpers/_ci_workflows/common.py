"""Shared utilities for CI workflow helper modules."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path


class CiWorkflowError(RuntimeError):
    """Raised when a CI workflow helper cannot complete its task."""


@contextlib.contextmanager
def patched_environment(
    entries: Mapping[str, str],
    *,
    unset: Iterable[str] = (),
) -> Iterator[None]:
    """Temporarily update the process environment."""
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in entries}
    removed: dict[str, str | None] = {key: os.environ.get(key) for key in unset}
    os.environ.update(entries)
    for key in unset:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for key, value in removed.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_github_env(entries: Mapping[str, str], github_env_file: Path | None) -> None:
    """Append environment exports for later GitHub Actions steps."""
    if github_env_file is None:
        return
    github_env_file.parent.mkdir(parents=True, exist_ok=True)
    with github_env_file.open("a", encoding="utf-8") as handle:
        for key, value in entries.items():
            handle.write(f"{key}={value}\n")


def append_github_path(path: Path, github_path_file: Path | None) -> None:
    """Append a directory to the GitHub Actions PATH file."""
    if github_path_file is None:
        return
    github_path_file.parent.mkdir(parents=True, exist_ok=True)
    with github_path_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{path}\n")


def run_checked(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and raise a CI-friendly error on failure."""
    try:
        return subprocess.run(
            list(command),
            check=True,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            capture_output=capture_output,
            timeout=timeout_seconds,
        )
    except subprocess.CalledProcessError as exc:
        details = ""
        if isinstance(exc.stderr, str) and exc.stderr.strip():
            details = exc.stderr.strip()
        elif isinstance(exc.stdout, str) and exc.stdout.strip():
            details = exc.stdout.strip()
        suffix = f": {details}" if details else ""
        raise CiWorkflowError(
            f"command failed ({' '.join(command)}) with exit code {exc.returncode}{suffix}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CiWorkflowError(
            f"command timed out ({' '.join(command)}) after {exc.timeout}s"
        ) from exc
    except OSError as exc:
        raise CiWorkflowError(f"failed to start command ({' '.join(command)}): {exc}") from exc


def download_file(url: str, destination: Path) -> Path:
    """Download a remote file to *destination*."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except OSError as exc:
        raise CiWorkflowError(f"failed to download {url}: {exc}") from exc
    return destination


def verify_sha256(path: Path, expected_sha256: str) -> None:
    """Verify that a file's SHA-256 digest matches *expected_sha256*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise CiWorkflowError(
            f"SHA-256 mismatch for {path}: expected {expected_sha256}, got {actual_sha256}"
        )


def extract_tar_member(archive_path: Path, member_name: str, destination: Path) -> Path:
    """Extract a single member from a tar archive to *destination*."""
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            member = archive.getmember(member_name)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise CiWorkflowError(f"archive member {member_name} is not a regular file")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                shutil.copyfileobj(extracted, handle)
    except KeyError as exc:
        raise CiWorkflowError(f"archive {archive_path} does not contain {member_name}") from exc
    except (OSError, tarfile.TarError) as exc:
        raise CiWorkflowError(
            f"failed to extract {member_name} from {archive_path}: {exc}"
        ) from exc

    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return destination
