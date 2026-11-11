"""Discover and rewrite tracked text that quotes the pinned OpenClaw/ACPX versions."""

from __future__ import annotations

import dataclasses
import functools
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final

_SEMVER: Final[str] = r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]*[0-9A-Za-z])?)"
VERSION_MENTION_PATTERNS: Final[Mapping[str, tuple[re.Pattern[str], ...]]] = {
    "openclaw": (
        re.compile(rf"\bopenclaw@{_SEMVER}"),
        re.compile(rf"\bOpenClaw (?:\| )?`{_SEMVER}`"),
        re.compile(rf"^OPENCLAW_VERSION={_SEMVER}$", re.MULTILINE),
    ),
    "acpx": (
        re.compile(rf"\bacpx@{_SEMVER}"),
        re.compile(rf"\bACPX (?:\| )?`{_SEMVER}`"),
    ),
}
# The packaged mirror is covered by the mirror contract, test fixtures may quote arbitrary
# versions, and the node-tools bundle itself is the source of truth.
_EXCLUDED_PREFIXES: Final[tuple[str, ...]] = (
    "src/clawops/assets/",
    "tests/suites/",
    "platform/node-tools/",
)
_EXCLUDED_SUFFIXES: Final[tuple[str, ...]] = ("package-lock.json", ".lock")


@dataclasses.dataclass(frozen=True, slots=True)
class VersionMention:
    """One tracked-text mention of a pinned node-tool version."""

    path: Path
    line: int
    package: str
    version: str


def _tracked_text_files(repo_root: Path) -> list[Path]:
    """Return repo-relative tracked files eligible for version scanning."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    files: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        relative = raw_path.decode("utf-8")
        if not relative or relative.startswith(_EXCLUDED_PREFIXES):
            continue
        if relative.endswith(_EXCLUDED_SUFFIXES) or not (repo_root / relative).is_file():
            continue
        files.append(Path(relative))
    return files


def _read_text(path: Path) -> str | None:
    """Return UTF-8 text, or None for binary files."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def find_version_mentions(repo_root: Path) -> list[VersionMention]:
    """Return every tracked mention of an OpenClaw/ACPX version."""
    mentions: list[VersionMention] = []
    for relative in _tracked_text_files(repo_root):
        text = _read_text(repo_root / relative)
        if text is None:
            continue
        for package, patterns in VERSION_MENTION_PATTERNS.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    mentions.append(
                        VersionMention(
                            path=relative,
                            line=text.count("\n", 0, match.start()) + 1,
                            package=package,
                            version=match.group(1),
                        )
                    )
    return mentions


def _with_version(match: re.Match[str], *, version: str) -> str:
    """Return the matched text with its captured version replaced by *version*."""
    offset = match.start(0)
    start, end = match.span(1)
    whole = match.group(0)
    return whole[: start - offset] + version + whole[end - offset :]


def rewrite_version_mentions(repo_root: Path, pins: Mapping[str, str]) -> list[Path]:
    """Rewrite every tracked mention to the pinned versions; return the changed files."""
    changed: list[Path] = []
    for relative in _tracked_text_files(repo_root):
        path = repo_root / relative
        original = _read_text(path)
        if original is None:
            continue
        updated = original
        for package, patterns in VERSION_MENTION_PATTERNS.items():
            version = pins[package]
            for pattern in patterns:
                updated = pattern.sub(functools.partial(_with_version, version=version), updated)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(relative)
    return changed
