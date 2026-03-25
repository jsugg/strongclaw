#!/usr/bin/env python3
"""Utility helpers for hosted fresh-host Docker image workflows."""

from __future__ import annotations

import argparse
import concurrent.futures
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence

IMAGE_LINE_RE = re.compile(r"^\s*image:\s*(?P<image>[^\s#]+)\s*$")


def _image_refs_from_compose(compose_path: pathlib.Path) -> list[str]:
    """Extract image references from one compose file in declaration order."""
    images: list[str] = []
    for raw_line in compose_path.read_text(encoding="utf-8").splitlines():
        match = IMAGE_LINE_RE.match(raw_line)
        if match is None:
            continue
        images.append(match.group("image"))
    if not images:
        raise ValueError(f"No image references found in {compose_path}.")
    return images


def collect_images(compose_paths: Sequence[pathlib.Path]) -> list[str]:
    """Collect unique image references from compose files in first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for compose_path in compose_paths:
        for image in _image_refs_from_compose(compose_path):
            if image in seen:
                continue
            seen.add(image)
            ordered.append(image)
    if not ordered:
        raise ValueError("No image references collected.")
    return ordered


def _pull_one_image(image: str) -> tuple[str, int, float, str]:
    """Pull one image and return its exit code, duration, and combined output."""
    started = time.monotonic()
    result = subprocess.run(
        ["docker", "pull", image],
        check=False,
        capture_output=True,
        text=True,
    )
    duration_seconds = time.monotonic() - started
    combined_output = "\n".join(
        chunk for chunk in (result.stdout.strip(), result.stderr.strip()) if chunk
    )
    return image, result.returncode, duration_seconds, combined_output


def pull_images(images: Sequence[str], *, parallelism: int) -> int:
    """Pull images concurrently, returning the aggregate process exit code."""
    if parallelism < 1:
        raise ValueError("parallelism must be positive")
    print(f"Pulling {len(images)} image(s) with parallelism={parallelism}.", flush=True)
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = [executor.submit(_pull_one_image, image) for image in images]
        for future in concurrent.futures.as_completed(futures):
            image, returncode, duration_seconds, output = future.result()
            duration = f"{duration_seconds:.1f}s"
            if returncode == 0:
                print(f"[ok] {image} in {duration}", flush=True)
                continue
            failures.append(image)
            print(f"[failed] {image} in {duration}", flush=True)
            if output:
                print(output, flush=True)
    if failures:
        print(f"Failed to pull {len(failures)} image(s): {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


def save_images(images: Sequence[str], *, output_path: pathlib.Path) -> int:
    """Save images into one tarball."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["docker", "image", "save", "-o", str(output_path), *images],
        check=False,
    )
    return result.returncode


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List compose images in first-seen order.")
    list_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)

    pull_parser = subparsers.add_parser("pull", help="Pull compose images concurrently.")
    pull_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)
    pull_parser.add_argument("--parallelism", type=int, default=3)

    save_parser = subparsers.add_parser("save", help="Save compose images into one tarball.")
    save_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)
    save_parser.add_argument("--output", required=True, type=pathlib.Path)

    return parser.parse_args(argv)


def _resolve_compose_paths(raw_paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    """Resolve compose file paths eagerly and validate existence."""
    paths: list[pathlib.Path] = []
    for raw_path in raw_paths:
        resolved = raw_path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        paths.append(resolved)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    """Run the workflow helper."""
    args = _parse_args(argv)
    compose_paths = _resolve_compose_paths(args.compose_files)
    images = collect_images(compose_paths)
    if args.command == "list":
        print("\n".join(images))
        return 0
    if args.command == "pull":
        return pull_images(images, parallelism=int(args.parallelism))
    if args.command == "save":
        return save_images(images, output_path=pathlib.Path(args.output))
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
