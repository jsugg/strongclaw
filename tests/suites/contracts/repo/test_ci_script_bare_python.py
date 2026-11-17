"""Contracts for CI helper scripts that run on the runner's bare Python.

Workflows call most `tests/scripts` helpers with the system `python3` before the project
environment exists (for example the fresh-host job budget, the first step of every feature lane).
Their whole import chain, including the `clawops` modules they load, must stay standard-library
only. `-S` drops site-packages, so a third-party import fails here as it would on the runner.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from tests.utils.helpers.repo import REPO_ROOT

# These run under `uv run` after the environment sync, so third-party imports are expected.
_PROJECT_ENV_SCRIPTS: Final[frozenset[str]] = frozenset(
    {"ci_gate.py", "compatibility_matrix.py", "feature_report.py", "security_workflow.py"}
)
_BARE_SCRIPTS: Final[list[Path]] = sorted(
    path
    for path in (REPO_ROOT / "tests" / "scripts").glob("*.py")
    if path.name not in _PROJECT_ENV_SCRIPTS
)


@pytest.mark.parametrize("script", _BARE_SCRIPTS, ids=[path.name for path in _BARE_SCRIPTS])
def test_bare_python_scripts_import_without_site_packages(script: Path) -> None:
    """The script's `--help` loads every import without any installed package."""
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
