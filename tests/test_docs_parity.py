"""Lightweight docs and config parity tests."""

from __future__ import annotations

import pathlib
import re

from clawops.context_service import load_config

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _official_markdown_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    root_markdown = [
        repo_root / "README.md",
        repo_root / "QUICKSTART.md",
        repo_root / "SETUP_GUIDE.md",
        repo_root / "USAGE_GUIDE.md",
    ]
    platform_markdown = [
        path
        for path in (repo_root / "platform").rglob("*.md")
        if "platform/plugins/" not in path.as_posix()
    ]
    return root_markdown + platform_markdown


def test_markdown_relative_links_resolve() -> None:
    repo_root = _repo_root()
    markdown_files = _official_markdown_files(repo_root)
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
    assert "platform/plugins/memory-lancedb-pro/**" in config.exclude_globs


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
