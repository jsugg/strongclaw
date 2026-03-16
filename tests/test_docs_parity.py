"""Lightweight docs and config parity tests."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

from clawops.context_service import load_config

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PYTEST_BASELINE_RE = re.compile(r"`(\d+) passed`")


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _collected_test_count(repo_root: pathlib.Path) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        check=True,
        capture_output=True,
        cwd=repo_root,
        env=env,
        text=True,
    )
    match = re.search(r"(\d+) tests collected", completed.stdout)
    assert match is not None, completed.stdout
    return int(match.group(1))


def test_markdown_relative_links_resolve() -> None:
    repo_root = _repo_root()
    markdown_files = list(repo_root.glob("*.md")) + list((repo_root / "platform").rglob("*.md"))
    for markdown_file in markdown_files:
        text = markdown_file.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            resolved = (markdown_file.parent / path_text).resolve()
            assert resolved.exists(), f"broken link in {markdown_file}: {target}"


def test_shipped_context_config_loads() -> None:
    repo_root = _repo_root()
    config = load_config(repo_root / "platform/configs/context/context-service.yaml")
    assert config.include_globs
    assert config.exclude_globs


def test_operator_docs_surface_platform_verification_commands() -> None:
    repo_root = _repo_root()
    quickstart = (repo_root / "QUICKSTART.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    setup = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")

    assert "./scripts/bootstrap/verify_sidecars.sh" in readme
    assert "./scripts/bootstrap/verify_sidecars.sh" in quickstart
    assert "./scripts/bootstrap/verify_channels.sh" in quickstart
    assert "./scripts/bootstrap/verify_channels.sh" in setup
    assert "./scripts/bootstrap/verify_observability.sh" in quickstart
    assert "./scripts/bootstrap/verify_observability.sh" in setup


def test_operator_docs_surface_current_trackers() -> None:
    repo_root = _repo_root()
    quickstart = (repo_root / "QUICKSTART.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    setup = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")

    assert "next-steps.md" in readme
    assert "platform/docs/MEMORY_V2.md" in readme
    assert "platform/docs/MEMORY_V2.md" in quickstart
    assert "platform/docs/MEMORY_V2.md" in setup


def test_next_steps_tracks_related_delivery_surfaces() -> None:
    repo_root = _repo_root()
    next_steps = (repo_root / "next-steps.md").read_text(encoding="utf-8")

    assert "memory-v2.md" in next_steps
    assert ".github/workflows/security.yml" in next_steps
    assert "scripts/recovery/backup_create.sh" in next_steps
    assert "tests/test_openclaw_shell_scripts.py" in next_steps


def test_next_steps_records_current_pytest_baseline() -> None:
    repo_root = _repo_root()
    next_steps = (repo_root / "next-steps.md").read_text(encoding="utf-8")
    recorded_counts = {int(match) for match in PYTEST_BASELINE_RE.findall(next_steps)}

    assert recorded_counts == {_collected_test_count(repo_root)}
