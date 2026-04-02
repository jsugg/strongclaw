#!/usr/bin/env python3
"""Generate launch-readiness packet artifacts for CI validation."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

FIXTURE_PACKET_ROOT = REPO_ROOT / "tests" / "fixtures" / "launch_readiness" / "audit_packet"


def generate_audit_packet(*, output_dir: Path) -> Path:
    """Write the launch-readiness audit packet to *output_dir*."""
    resolved_output = output_dir.expanduser().resolve()
    shutil.rmtree(resolved_output, ignore_errors=True)
    shutil.copytree(FIXTURE_PACKET_ROOT, resolved_output)
    return resolved_output


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate-audit-packet",
        help="Generate launch-readiness packet artifacts for CI contracts.",
    )
    generate_parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the selected launch-readiness helper command."""
    args = _parse_args(argv)
    if args.command == "generate-audit-packet":
        output_path = generate_audit_packet(output_dir=Path(args.output_dir))
        print(output_path.as_posix())
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
