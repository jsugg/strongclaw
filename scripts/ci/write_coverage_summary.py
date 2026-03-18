#!/usr/bin/env python3
"""Append the line coverage percentage to the GitHub Actions job summary."""

from __future__ import annotations

import argparse
import os
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_xml", type=Path, help="Path to the Cobertura coverage.xml file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        raise RuntimeError("GITHUB_STEP_SUMMARY is required to publish the coverage summary.")

    root = ET.parse(args.coverage_xml).getroot()
    coverage = float(root.attrib["line-rate"]) * 100
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(f"Coverage: {coverage:.2f}%\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
