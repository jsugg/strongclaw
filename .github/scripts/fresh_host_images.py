#!/usr/bin/env python3
"""Utility helpers for hosted fresh-host Docker image workflows."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

IMAGE_LINE_RE = re.compile(r"^\s*image:\s*(?P<image>[^\s#]+)\s*$")
CACHE_TAR_NAME = "all-images.tar"
CACHE_MANIFEST_NAME = "all-images.json"


def _log(message: str) -> None:
    """Emit one CI-friendly debug line."""
    print(f"[fresh-host-images] {message}", flush=True)


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


@dataclass(slots=True)
class EnsureReport:
    """Structured report describing one image ensure run."""

    compose_files: list[str]
    images: list[str]
    cache_requested: bool
    cache_available: bool
    cache_loaded: bool
    cache_saved: bool
    local_before: list[str]
    missing_before_load: list[str]
    missing_after_load: list[str]
    pulled_images: list[str]
    missing_after_pull: list[str]
    pull_parallelism: int
    cache_tar_path: str | None
    cache_manifest_path: str | None
    created_at: str


def _cache_tar_path(cache_dir: pathlib.Path) -> pathlib.Path:
    """Return the hosted fresh-host image tarball path."""
    return cache_dir / CACHE_TAR_NAME


def _cache_manifest_path(cache_dir: pathlib.Path) -> pathlib.Path:
    """Return the hosted fresh-host image manifest path."""
    return cache_dir / CACHE_MANIFEST_NAME


def _read_cache_manifest(cache_dir: pathlib.Path) -> dict[str, object]:
    """Read the cache manifest if one exists."""
    manifest_path = _cache_manifest_path(cache_dir)
    if not manifest_path.is_file():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _path_size_mib(path: pathlib.Path) -> float:
    """Return the size of one file in MiB."""
    return path.stat().st_size / (1024 * 1024)


def list_local_images(images: Sequence[str]) -> list[str]:
    """Return images that are already present in the local Docker daemon."""
    present: list[str] = []
    for image in images:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            present.append(image)
    return present


def load_image_cache(cache_dir: pathlib.Path) -> bool:
    """Load cached images from one tarball if it exists."""
    tar_path = _cache_tar_path(cache_dir)
    if not tar_path.is_file():
        return False
    started = time.monotonic()
    result = subprocess.run(
        ["docker", "load", "-i", str(tar_path)],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker load failed for {tar_path}")
    _log(
        f"Loaded Docker image cache from {tar_path} in {time.monotonic() - started:.1f}s "
        f"({ _path_size_mib(tar_path):.1f} MiB)."
    )
    return True


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


def save_image_cache(cache_dir: pathlib.Path, images: Sequence[str]) -> None:
    """Persist the current image set into the cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tar_path = _cache_tar_path(cache_dir)
    manifest_path = _cache_manifest_path(cache_dir)
    started = time.monotonic()
    save_result = save_images(images, output_path=tar_path)
    if save_result != 0:
        raise RuntimeError(f"docker image save failed for {tar_path}")
    manifest = {
        "createdAt": datetime.now(tz=UTC).isoformat(),
        "images": list(images),
        "tarPath": str(tar_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _log(
        f"Saved Docker image cache to {tar_path} in {time.monotonic() - started:.1f}s "
        f"({ _path_size_mib(tar_path):.1f} MiB)."
    )


def _write_report(report: EnsureReport, report_path: pathlib.Path) -> None:
    """Persist one ensure report as JSON."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def ensure_images(
    compose_paths: Sequence[pathlib.Path],
    *,
    parallelism: int,
    cache_dir: pathlib.Path | None = None,
    report_path: pathlib.Path | None = None,
) -> int:
    """Ensure compose images exist locally, using a cache tarball when available."""
    images = collect_images(compose_paths)
    _log(f"Collected {len(images)} image(s): {', '.join(images)}")
    local_before = list_local_images(images)
    missing_before_load = [image for image in images if image not in local_before]
    _log(
        f"Local images before cache load: {len(local_before)} present, {len(missing_before_load)} missing."
    )

    cache_requested = cache_dir is not None
    cache_available = False
    cache_loaded = False
    cache_saved = False
    if cache_dir is not None:
        cache_available = _cache_tar_path(cache_dir).is_file()
        _log(f"Cache requested from {cache_dir}; tar present={cache_available}.")
        manifest = _read_cache_manifest(cache_dir)
        if manifest:
            manifest_images = manifest.get("images")
            manifest_image_count = len(manifest_images) if isinstance(manifest_images, list) else 0
            _log(
                "Cache manifest: "
                f"{manifest_image_count} image(s), createdAt={manifest.get('createdAt')}"
            )
    if missing_before_load and cache_dir is not None and cache_available:
        cache_loaded = load_image_cache(cache_dir)

    local_after_load = list_local_images(images)
    missing_after_load = [image for image in images if image not in local_after_load]
    _log(f"After cache load: {len(local_after_load)} present, {len(missing_after_load)} missing.")
    pulled_images: list[str] = []
    if missing_after_load:
        pull_result = pull_images(missing_after_load, parallelism=parallelism)
        if pull_result != 0:
            return pull_result
        pulled_images = list(missing_after_load)
    else:
        _log("No image pulls were required after cache load.")

    local_after_pull = list_local_images(images)
    missing_after_pull = [image for image in images if image not in local_after_pull]
    _log(f"After pull: {len(local_after_pull)} present, {len(missing_after_pull)} missing.")
    if missing_after_pull:
        print(
            "Images remain unavailable after cache load and pull: " + ", ".join(missing_after_pull),
            file=sys.stderr,
        )
        return 1

    if cache_dir is not None and (not cache_available or bool(pulled_images)):
        save_image_cache(cache_dir, images)
        cache_saved = True

    report = EnsureReport(
        compose_files=[str(path) for path in compose_paths],
        images=list(images),
        cache_requested=cache_requested,
        cache_available=cache_available,
        cache_loaded=cache_loaded,
        cache_saved=cache_saved,
        local_before=local_before,
        missing_before_load=missing_before_load,
        missing_after_load=missing_after_load,
        pulled_images=pulled_images,
        missing_after_pull=missing_after_pull,
        pull_parallelism=parallelism,
        cache_tar_path=str(_cache_tar_path(cache_dir)) if cache_dir is not None else None,
        cache_manifest_path=str(_cache_manifest_path(cache_dir)) if cache_dir is not None else None,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    if report_path is not None:
        _write_report(report, report_path)
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List compose images in first-seen order.")
    list_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)

    pull_parser = subparsers.add_parser("pull", help="Pull compose images concurrently.")
    pull_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)
    pull_parser.add_argument("--parallelism", type=int, default=3)

    ensure_parser = subparsers.add_parser(
        "ensure",
        help="Load cached images when available and pull only missing images.",
    )
    ensure_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)
    ensure_parser.add_argument("--parallelism", type=int, default=3)
    ensure_parser.add_argument("--cache-dir", type=pathlib.Path)
    ensure_parser.add_argument("--report-path", type=pathlib.Path)

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
    if args.command == "ensure":
        return ensure_images(
            compose_paths,
            parallelism=int(args.parallelism),
            cache_dir=(
                pathlib.Path(args.cache_dir).expanduser().resolve()
                if args.cache_dir is not None
                else None
            ),
            report_path=(
                pathlib.Path(args.report_path).expanduser().resolve()
                if args.report_path is not None
                else None
            ),
        )
    if args.command == "save":
        return save_images(images, output_path=pathlib.Path(args.output))
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
