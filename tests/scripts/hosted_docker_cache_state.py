#!/usr/bin/env python3
"""Semantic CLI for hosted Docker cache-state classification."""

from __future__ import annotations

import argparse
from pathlib import Path


def classify_cache_state(*, cache_hit: str, cache_matched_key: str) -> str:
    """Classify cache state as hit, partial, or miss."""
    normalized_hit = cache_hit.strip().lower()
    if normalized_hit == "true":
        return "hit"
    if cache_matched_key.strip():
        return "partial"
    return "miss"


def _append_github_env(github_env_file: Path | None, *, key: str, value: str) -> None:
    """Append one environment export to the GitHub env file when configured."""
    if github_env_file is None:
        return
    github_env_file.parent.mkdir(parents=True, exist_ok=True)
    with github_env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser(
        "classify",
        help="Classify cache state from actions/cache outputs.",
    )
    classify_parser.add_argument("--cache-hit", required=True)
    classify_parser.add_argument("--cache-matched-key", default="")
    classify_parser.add_argument("--github-env-file", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested cache-state subcommand."""
    args = _parse_args(argv)
    if args.command == "classify":
        state = classify_cache_state(
            cache_hit=str(args.cache_hit),
            cache_matched_key=str(args.cache_matched_key),
        )
        _append_github_env(
            (
                Path(args.github_env_file).expanduser().resolve()
                if args.github_env_file is not None
                else None
            ),
            key="FRESH_HOST_IMAGE_CACHE_STATE",
            value=state,
        )
        print(state)
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
