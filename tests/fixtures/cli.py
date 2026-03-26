"""Pytest fixtures for CLI shim tests."""

from __future__ import annotations

import os
import pathlib

import pytest

from tests.utils.helpers.cli import (
    PathPrepender,
)


@pytest.fixture
def cli_bin_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Provide an isolated bin directory for CLI shims."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return bin_dir


@pytest.fixture
def prepend_path(monkeypatch: pytest.MonkeyPatch) -> PathPrepender:
    """Prepend one bin directory to PATH for the duration of a test."""

    def _prepend(path: pathlib.Path) -> None:
        current = os.environ.get("PATH", "")
        updated = path.as_posix() if not current else f"{path}{os.pathsep}{current}"
        monkeypatch.setenv("PATH", updated)

    return _prepend


__all__ = [
    "cli_bin_dir",
    "prepend_path",
]
