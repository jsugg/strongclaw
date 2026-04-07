#!/usr/bin/env python3
"""Semantic CLI for hosted fresh-host Docker runtime operations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers.fresh_host import FreshHostError  # noqa: E402
from tests.utils.helpers.hosted_docker import (  # noqa: E402
    collect_runtime_diagnostics,
    ensure_images,
    install_runtime,
    restore_image_cache,
    save_image_cache,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install-runtime", help="Install the hosted runtime.")
    install_parser.add_argument("--context", type=Path, required=True)
    install_parser.add_argument("--github-env-file", type=Path)

    ensure_parser = subparsers.add_parser(
        "ensure-images", help="Ensure compose images exist locally."
    )
    ensure_parser.add_argument("--context", type=Path, required=True)

    restore_parser = subparsers.add_parser(
        "restore-image-cache",
        help="Restore Docker images from the workflow cache archive.",
    )
    restore_parser.add_argument("--context", type=Path, required=True)
    restore_parser.add_argument("--github-env-file", type=Path)

    save_parser = subparsers.add_parser(
        "save-image-cache",
        help="Save Docker images into the workflow cache archive.",
    )
    save_parser.add_argument("--context", type=Path, required=True)

    diagnostics_parser = subparsers.add_parser(
        "collect-diagnostics",
        help="Collect hosted runtime diagnostics.",
    )
    diagnostics_parser.add_argument("--context", type=Path, required=True)

    return parser.parse_args(argv)


def _append_github_env(github_env_file: Path | None, *, key: str, value: str) -> None:
    """Append one environment export to the GitHub env file when configured."""
    if github_env_file is None:
        return
    github_env_file.parent.mkdir(parents=True, exist_ok=True)
    with github_env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    """Run the requested hosted-docker subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "install-runtime":
            install_runtime(
                Path(args.context).expanduser().resolve(),
                github_env_file=(
                    Path(args.github_env_file).expanduser().resolve()
                    if args.github_env_file is not None
                    else None
                ),
            )
            return 0
        if args.command == "ensure-images":
            ensure_images(Path(args.context).expanduser().resolve())
            return 0
        if args.command == "restore-image-cache":
            restored = restore_image_cache(Path(args.context).expanduser().resolve())
            _append_github_env(
                (
                    Path(args.github_env_file).expanduser().resolve()
                    if args.github_env_file is not None
                    else None
                ),
                key="FRESH_HOST_IMAGE_CACHE_RESTORED",
                value=str(restored).lower(),
            )
            return 0
        if args.command == "save-image-cache":
            save_image_cache(Path(args.context).expanduser().resolve())
            return 0
        if args.command == "collect-diagnostics":
            collect_runtime_diagnostics(Path(args.context).expanduser().resolve())
            return 0
    except FreshHostError as exc:
        print(f"hosted-docker error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
