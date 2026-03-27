"""Reusable helpers for devflow tests."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

from tests.utils.helpers.cli import write_fake_acpx, write_status_script
from tests.utils.helpers.repo import REPO_ROOT

FIXTURE_REPOS_ROOT = REPO_ROOT / "tests" / "fixtures" / "repos"


def write_strongclaw_shaped_repo(repo_root: pathlib.Path) -> None:
    """Create a minimal Strongclaw-shaped repository."""
    (repo_root / "src" / "clawops").mkdir(parents=True, exist_ok=True)
    (repo_root / "Makefile").write_text("install:\n\tuv sync --locked\n", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (repo_root / "README.md").write_text("# Fixture repo\n", encoding="utf-8")
    (repo_root / "src" / "clawops" / "__init__.py").write_text("", encoding="utf-8")


def install_fake_devflow_backends(bin_dir: pathlib.Path) -> None:
    """Install fake ACPX, Codex, and Claude binaries into *bin_dir*."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    write_fake_acpx(bin_dir)
    write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    write_status_script(bin_dir, "claude", stdout_text='{"status":"authenticated"}')


def init_git_repo(repo_root: pathlib.Path) -> None:
    """Initialize a git repository with one initial commit."""
    subprocess.run(
        ["git", "init", "-b", "main", str(repo_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "tests@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
        text=True,
    )


def copy_fixture_repo(name: str, destination: pathlib.Path) -> pathlib.Path:
    """Copy one fixture repository into *destination* and return it."""
    source = FIXTURE_REPOS_ROOT / name
    shutil.copytree(source, destination)
    return destination
