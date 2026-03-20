"""CLI entrypoints for guided StrongClaw setup and doctor workflows."""

from __future__ import annotations

import os
import pathlib
import subprocess
from collections.abc import Sequence

DEFAULT_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _resolve_script(env_name: str, relative_path: str) -> pathlib.Path:
    """Resolve an overrideable shell entrypoint."""
    override = os.environ.get(env_name)
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return (DEFAULT_REPO_ROOT / relative_path).resolve()


def _exec_script(script_path: pathlib.Path, argv: Sequence[str] | None) -> int:
    """Run a shell entrypoint with inherited stdio."""
    if not script_path.exists():
        print(f"missing script: {script_path}")
        return 1
    result = subprocess.run(_script_command(script_path, argv), check=False)
    return int(result.returncode)


def _script_command(script_path: pathlib.Path, argv: Sequence[str] | None) -> list[str]:
    """Build a resilient command line for a local setup shell entrypoint."""
    args = list(argv or ())
    if script_path.suffix == ".sh":
        return ["/bin/bash", str(script_path), *args]
    return [str(script_path), *args]


def setup_main(argv: list[str] | None = None) -> int:
    """Run the guided setup shell workflow."""
    script_path = _resolve_script("CLAWOPS_SETUP_SCRIPT", "scripts/bootstrap/setup.sh")
    return _exec_script(script_path, argv)


def doctor_main(argv: list[str] | None = None) -> int:
    """Run the deep StrongClaw doctor shell workflow."""
    script_path = _resolve_script("CLAWOPS_DOCTOR_SCRIPT", "scripts/bootstrap/doctor_strongclaw.sh")
    return _exec_script(script_path, argv)
