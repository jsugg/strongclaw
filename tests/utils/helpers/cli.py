"""Reusable CLI shim helpers for tests."""

from __future__ import annotations

import pathlib
import shutil
from collections.abc import Callable

type PathPrepender = Callable[[pathlib.Path], None]


def write_status_script(
    bin_dir: pathlib.Path,
    name: str,
    *,
    stdout_text: str,
    exit_code: int = 0,
) -> None:
    """Write a fake login-status command for backend readiness tests."""
    target = bin_dir / name
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == *"login status"* ]] || [[ "$*" == *"auth status"* ]]; then\n'
        f"  printf '%s\\n' {stdout_text!r}\n"
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def write_fake_acpx(bin_dir: pathlib.Path, *, exit_code: int = 0) -> None:
    """Write a fake ACPX executable for orchestration tests."""
    target = bin_dir / "acpx"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf 'fake-acpx %s\\n' \"$*\"\n"
        "printf 'stderr from fake-acpx\\n' >&2\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def require_system_executable(name: str) -> pathlib.Path:
    """Resolve one required system executable or fail the test eagerly."""
    executable = shutil.which(name)
    if executable is None:
        raise AssertionError(f"{name} is required for this test")
    return pathlib.Path(executable)


def symlink_executable(
    bin_dir: pathlib.Path,
    target: pathlib.Path,
    *,
    name: str | None = None,
) -> pathlib.Path:
    """Expose one real executable in a fake bin directory."""
    destination = bin_dir / (target.name if name is None else name)
    destination.symlink_to(target)
    return destination
