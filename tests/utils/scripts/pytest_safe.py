"""Run pytest in a subprocess with a bounded wall-clock timeout."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from collections.abc import Sequence


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
        return
    os.killpg(process.pid, signal.SIGTERM)


def run_pytest(args: Sequence[str], *, timeout_seconds: float) -> int:
    """Run pytest with a timeout and propagate the resulting exit code."""
    command = [sys.executable, "-m", "pytest", *args]
    process = subprocess.Popen(
        command,
        text=True,
        start_new_session=os.name != "nt",
    )
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            return process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
            return 124


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run pytest safely."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Wall-clock timeout in seconds before pytest is terminated.",
    )
    parser.add_argument("pytest_args", nargs="*", help="Arguments forwarded to pytest.")
    args = parser.parse_args(argv)
    return run_pytest(args.pytest_args, timeout_seconds=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
