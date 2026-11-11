#!/usr/bin/env python3
"""Join feature-verification lane results with the feature matrix into a verdict report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers.feature_verification import (  # noqa: E402
    FeatureVerificationError,
    write_lane_report,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest", type=Path, default=REPO_ROOT / "tests" / "feature_matrix.yaml"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-file", type=Path)
    parser.add_argument(
        "--fail-on-failures",
        action="store_true",
        help="Exit non-zero when any feature is FAIL.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write the report and optionally fail on FAIL verdicts."""
    args = _parse_args(argv)
    try:
        failures = write_lane_report(
            results_dir=Path(args.results_dir).expanduser().resolve(),
            manifest_path=Path(args.manifest).expanduser().resolve(),
            output_path=Path(args.output).expanduser().resolve(),
            summary_file=(
                Path(args.summary_file).expanduser().resolve()
                if args.summary_file is not None
                else None
            ),
        )
    except FeatureVerificationError as exc:
        print(f"feature-report error: {exc}", file=sys.stderr)
        return 1
    print(f"feature verification: {failures} feature(s) FAIL")
    return 1 if failures and bool(args.fail_on_failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
