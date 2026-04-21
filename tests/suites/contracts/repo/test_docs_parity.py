"""Lightweight docs and config parity tests."""

from __future__ import annotations

import pathlib
import re

from clawops.context.codebase.service import load_config
from tests.utils.helpers.repo import REPO_ROOT

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _official_markdown_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    root_markdown = [
        repo_root / "README.md",
        repo_root / "QUICKSTART.md",
        repo_root / "SETUP_GUIDE.md",
        repo_root / "USAGE_GUIDE.md",
    ]
    docs_markdown = (
        [path for path in (repo_root / "docs").rglob("*.md") if path.is_file()]
        if (repo_root / "docs").exists()
        else []
    )
    platform_markdown = [
        path
        for path in (repo_root / "platform").rglob("*.md")
        if "platform/plugins/" not in path.as_posix()
    ]
    return root_markdown + docs_markdown + platform_markdown


def test_markdown_relative_links_resolve() -> None:
    markdown_files = _official_markdown_files(REPO_ROOT)
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
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")
    assert config.include_globs
    assert config.exclude_globs
    assert "src/clawops/assets/**" in config.exclude_globs
    assert "platform/plugins/memory-lancedb-pro/**" in config.exclude_globs


def test_operator_docs_no_longer_surface_root_shell_entrypoints() -> None:
    for markdown_file in _official_markdown_files(REPO_ROOT):
        text = markdown_file.read_text(encoding="utf-8")
        assert "./scripts/" not in text
