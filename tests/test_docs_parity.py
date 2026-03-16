"""Lightweight docs and config parity tests."""

from __future__ import annotations

import pathlib
import re

from clawops.context_service import load_config

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_markdown_relative_links_resolve() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
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
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    config = load_config(repo_root / "platform/configs/context/context-service.yaml")
    assert config.include_globs
    assert config.exclude_globs


def test_operator_docs_surface_platform_verification_commands() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
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
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    quickstart = (repo_root / "QUICKSTART.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    setup = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")

    assert "next-steps.md" in readme
    assert "platform/docs/MEMORY_V2.md" in readme
    assert "platform/docs/MEMORY_V2.md" in quickstart
    assert "platform/docs/MEMORY_V2.md" in setup


def test_next_steps_tracks_related_delivery_surfaces() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    next_steps = (repo_root / "next-steps.md").read_text(encoding="utf-8")

    assert "memory-v2.md" in next_steps
    assert ".github/workflows/security.yml" in next_steps
    assert "scripts/recovery/backup_create.sh" in next_steps
    assert "tests/test_openclaw_shell_scripts.py" in next_steps
