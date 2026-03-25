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
    local_before: list[str]
    missing_before_pull: list[str]
    pulled_images: list[str]
    missing_after_pull: list[str]
    pull_parallelism: int
    pull_attempt_count: int
    retried_images: list[str]
    failure_reason: str | None
    created_at: str


@dataclass(slots=True)
class PullReport:
    """Structured report describing one image pull sequence."""

    exit_code: int
    pulled_images: list[str]
    failed_images: list[str]
    attempt_count: int
    retried_images: list[str]


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


def pull_images(
    images: Sequence[str],
    *,
    parallelism: int,
    max_attempts: int = 3,
) -> PullReport:
    """Pull images with retries, returning a structured outcome report."""
    if parallelism < 1:
        raise ValueError("parallelism must be positive")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    outstanding = list(images)
    pulled_images: list[str] = []
    retried_images: list[str] = []
    seen_retries: set[str] = set()
    attempt_parallelism = parallelism
    attempt_count = 0

    while outstanding and attempt_count < max_attempts:
        attempt_count += 1
        print(
            f"Pulling {len(outstanding)} image(s) with parallelism={attempt_parallelism} "
            f"(attempt {attempt_count}/{max_attempts}).",
            flush=True,
        )
        failures: list[str] = []
        worker_count = min(attempt_parallelism, len(outstanding))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_pull_one_image, image) for image in outstanding]
            for future in concurrent.futures.as_completed(futures):
                image, returncode, duration_seconds, output = future.result()
                duration = f"{duration_seconds:.1f}s"
                if returncode == 0:
                    pulled_images.append(image)
                    print(f"[ok] {image} in {duration}", flush=True)
                    continue
                failures.append(image)
                print(f"[failed] {image} in {duration}", flush=True)
                if output:
                    print(output, flush=True)
        outstanding = failures
        if not outstanding or attempt_count >= max_attempts:
            break
        for image in outstanding:
            if image in seen_retries:
                continue
            seen_retries.add(image)
            retried_images.append(image)
        next_parallelism = max(1, attempt_parallelism // 2)
        if next_parallelism != attempt_parallelism:
            _log(
                "Retrying failed pulls with reduced parallelism "
                f"{attempt_parallelism}->{next_parallelism}."
            )
        attempt_parallelism = next_parallelism
        backoff_seconds = min(10, 2 * attempt_count)
        _log(
            f"Retrying {len(outstanding)} image(s) after {backoff_seconds}s backoff: "
            + ", ".join(outstanding)
        )
        time.sleep(backoff_seconds)

    if outstanding:
        print(
            f"Failed to pull {len(outstanding)} image(s): {', '.join(outstanding)}",
            file=sys.stderr,
        )
        return PullReport(
            exit_code=1,
            pulled_images=pulled_images,
            failed_images=outstanding,
            attempt_count=attempt_count,
            retried_images=retried_images,
        )
    return PullReport(
        exit_code=0,
        pulled_images=pulled_images,
        failed_images=[],
        attempt_count=attempt_count,
        retried_images=retried_images,
    )


def _write_report(report: EnsureReport, report_path: pathlib.Path) -> None:
    """Persist one ensure report as JSON."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_images(
    compose_paths: Sequence[pathlib.Path],
    *,
    parallelism: int,
    max_attempts: int = 3,
    report_path: pathlib.Path | None = None,
) -> int:
    """Ensure compose images exist locally, pulling only the missing refs."""
    images = collect_images(compose_paths)
    _log(f"Collected {len(images)} image(s): {', '.join(images)}")
    local_before = list_local_images(images)
    missing_before_pull = [image for image in images if image not in local_before]
    _log(
        "Local images before pull: "
        f"{len(local_before)} present, {len(missing_before_pull)} missing."
    )
    if missing_before_pull:
        _log("Missing before pull: " + ", ".join(missing_before_pull))

    pulled_images: list[str] = []
    pull_attempt_count = 0
    retried_images: list[str] = []
    failure_reason: str | None = None
    exit_code = 0

    if missing_before_pull:
        pull_result = pull_images(
            missing_before_pull,
            parallelism=parallelism,
            max_attempts=max_attempts,
        )
        pulled_images = list(pull_result.pulled_images)
        pull_attempt_count = pull_result.attempt_count
        retried_images = list(pull_result.retried_images)
        if pull_result.exit_code != 0:
            failure_reason = "docker pull failed"
            exit_code = pull_result.exit_code
    else:
        _log("No image pulls were required.")

    local_after_pull = list_local_images(images)
    missing_after_pull = [image for image in images if image not in local_after_pull]
    _log(f"After pull: {len(local_after_pull)} present, {len(missing_after_pull)} missing.")
    if missing_after_pull:
        _log("Missing after pull: " + ", ".join(missing_after_pull))
        if failure_reason is None:
            failure_reason = "images remain unavailable after pull"
            exit_code = 1

    report = EnsureReport(
        compose_files=[str(path) for path in compose_paths],
        images=list(images),
        local_before=local_before,
        missing_before_pull=missing_before_pull,
        pulled_images=pulled_images,
        missing_after_pull=missing_after_pull,
        pull_parallelism=parallelism,
        pull_attempt_count=pull_attempt_count,
        retried_images=retried_images,
        failure_reason=failure_reason,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    if report_path is not None:
        _write_report(report, report_path)
    return exit_code


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List compose images in first-seen order.")
    list_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)

    pull_parser = subparsers.add_parser("pull", help="Pull compose images concurrently.")
    pull_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)
    pull_parser.add_argument("--parallelism", type=int, default=2)
    pull_parser.add_argument("--max-attempts", type=int, default=3)

    ensure_parser = subparsers.add_parser("ensure", help="Pull missing compose images.")
    ensure_parser.add_argument("compose_files", nargs="+", type=pathlib.Path)
    ensure_parser.add_argument("--parallelism", type=int, default=2)
    ensure_parser.add_argument("--max-attempts", type=int, default=3)
    ensure_parser.add_argument("--report-path", type=pathlib.Path)

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
        return pull_images(
            images,
            parallelism=int(args.parallelism),
            max_attempts=int(args.max_attempts),
        ).exit_code
    if args.command == "ensure":
        return ensure_images(
            compose_paths,
            parallelism=int(args.parallelism),
            report_path=(
                pathlib.Path(args.report_path).expanduser().resolve()
                if args.report_path is not None
                else None
            ),
            max_attempts=int(args.max_attempts),
        )
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
