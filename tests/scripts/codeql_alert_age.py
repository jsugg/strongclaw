#!/usr/bin/env python3
"""Generate report-only JSON/Markdown age evidence for open CodeQL alerts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers._ci_workflows.codeql_alert_age import (  # noqa: E402
    build_codeql_alert_age_report,
    load_codeql_alert_payload,
    render_codeql_alert_age_markdown,
    write_codeql_alert_age_report,
)
from tests.utils.helpers._ci_workflows.common import CiWorkflowError  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_ROOT / ".artifacts/codeql-alert-age/report.json",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=REPO_ROOT / ".artifacts/codeql-alert-age/report.md",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Generate report artifacts without changing alert state or enforcing age."""
    args = _parse_args(argv)
    try:
        report = build_codeql_alert_age_report(
            load_codeql_alert_payload(Path(args.input_json)),
            repository=str(args.repository),
            stale_days=int(args.stale_days),
        )
        write_codeql_alert_age_report(
            report,
            json_path=Path(args.output_json),
            markdown_path=Path(args.output_markdown),
        )
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(render_codeql_alert_age_markdown(report))
        print(f"CodeQL report-only inventory: open={len(report.alerts)} stale={report.stale_count}")
        return 0
    except (CiWorkflowError, OSError) as exc:
        print(f"CodeQL alert-age report failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
