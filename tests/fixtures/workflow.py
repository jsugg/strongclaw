"""Workflow test support helpers."""

from __future__ import annotations

import pathlib


def write_status_script(
    bin_dir: pathlib.Path,
    name: str,
    *,
    stdout_text: str,
) -> None:
    """Write a fake CLI status command for workflow dispatch tests."""
    target = bin_dir / name
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == *"login status"* ]] || [[ "$*" == *"auth status"* ]]; then\n'
        f"  printf '%s\\n' {stdout_text!r}\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def write_fake_acpx(bin_dir: pathlib.Path) -> None:
    """Write a fake ACPX binary for workflow dispatch tests."""
    target = bin_dir / "acpx"
    target.write_text(
        "#!/usr/bin/env bash\n" "set -euo pipefail\n" "printf 'fake-acpx %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
