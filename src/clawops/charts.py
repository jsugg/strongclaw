"""Chart harness result files into a simple pass/fail visualization."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import matplotlib.pyplot as plt


def load_results(path: pathlib.Path) -> tuple[list[str], list[int]]:
    """Load IDs and pass/fail values from a JSONL file."""
    labels: list[str] = []
    values: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        labels.append(str(item["id"]))
        values.append(1 if item["passed"] else 0)
    return labels, values


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse chart CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    labels, values = load_results(args.input)
    fig: Any = plt.figure(figsize=(max(6, len(labels) * 1.2), 4))
    ax: Any = fig.add_subplot(111)
    ax.bar(labels, values)
    ax.set_ylim(0, 1.2)
    ax.set_ylabel("pass=1 / fail=0")
    ax.set_title("Harness results")
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output)
    return 0
