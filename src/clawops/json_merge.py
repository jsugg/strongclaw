"""Merge JSON documents for staged OpenClaw config overlays."""

from __future__ import annotations

import argparse
import pathlib
from typing import Any

from clawops.common import deep_merge, dump_json, load_json, write_text


def merge_documents(base: Any, overlays: list[Any]) -> Any:
    """Apply overlays to *base* in sequence."""
    merged = base
    for overlay in overlays:
        merged = deep_merge(merged, overlay)
    return merged


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=pathlib.Path)
    parser.add_argument("--overlay", required=True, nargs="+", type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    base = load_json(args.base)
    overlays = [load_json(path) for path in args.overlay]
    merged = merge_documents(base, overlays)
    write_text(args.output, dump_json(merged))
    return 0
