"""Root discovery helpers for StrongClaw and generic project CLIs."""

from __future__ import annotations

import pathlib
from collections.abc import Iterable, Sequence

STRONGCLAW_REPO_MARKERS: tuple[pathlib.Path, ...] = (
    pathlib.Path("pyproject.toml"),
    pathlib.Path("platform"),
    pathlib.Path("src/clawops"),
)
PROJECT_ROOT_MARKER_GROUPS: tuple[tuple[pathlib.Path, ...], ...] = (
    (pathlib.Path(".git"),),
    (pathlib.Path("pyproject.toml"),),
)
DEFAULT_SOURCE_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _resolve_path(value: pathlib.Path | str) -> pathlib.Path:
    """Return one expanded absolute path."""
    return pathlib.Path(value).expanduser().resolve()


def _candidate_roots(start: pathlib.Path) -> Iterable[pathlib.Path]:
    """Yield *start* and each parent directory."""
    yield start
    yield from start.parents


def _matches_markers(root: pathlib.Path, markers: Sequence[pathlib.Path]) -> bool:
    """Return whether *root* contains every required marker."""
    return all((root / marker).exists() for marker in markers)


def _discover_root(
    start: pathlib.Path,
    *,
    marker_groups: Sequence[Sequence[pathlib.Path]],
) -> pathlib.Path | None:
    """Walk upward from *start* until one marker group matches."""
    for candidate in _candidate_roots(start):
        if any(_matches_markers(candidate, group) for group in marker_groups):
            return candidate
    return None


def discover_strongclaw_repo_root(start: pathlib.Path | str | None = None) -> pathlib.Path | None:
    """Return the nearest StrongClaw repo root at or above *start*."""
    resolved_start = _resolve_path(pathlib.Path.cwd() if start is None else pathlib.Path(start))
    return _discover_root(resolved_start, marker_groups=(STRONGCLAW_REPO_MARKERS,))


def resolve_strongclaw_repo_root(
    repo_root: pathlib.Path | str | None = None,
    *,
    cwd: pathlib.Path | str | None = None,
    fallback: pathlib.Path | str | None = DEFAULT_SOURCE_REPO_ROOT,
) -> pathlib.Path:
    """Return the explicit or discovered StrongClaw repo root."""
    if repo_root is not None:
        return _resolve_path(repo_root)
    detected = discover_strongclaw_repo_root(cwd)
    if detected is not None:
        return detected
    if fallback is not None:
        resolved_fallback = _resolve_path(fallback)
        if _matches_markers(resolved_fallback, STRONGCLAW_REPO_MARKERS):
            return resolved_fallback
    raise FileNotFoundError(
        "Could not infer the StrongClaw repo root from the current working directory; "
        "pass --repo-root explicitly."
    )


def discover_project_root(start: pathlib.Path | str | None = None) -> pathlib.Path | None:
    """Return the nearest generic project root at or above *start*."""
    resolved_start = _resolve_path(pathlib.Path.cwd() if start is None else pathlib.Path(start))
    return _discover_root(resolved_start, marker_groups=PROJECT_ROOT_MARKER_GROUPS)


def resolve_project_root(
    project_root: pathlib.Path | str | None = None,
    *,
    cwd: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """Return the explicit or discovered generic project root."""
    if project_root is not None:
        return _resolve_path(project_root)
    resolved_cwd = _resolve_path(pathlib.Path.cwd() if cwd is None else pathlib.Path(cwd))
    detected = discover_project_root(resolved_cwd)
    if detected is not None:
        return detected
    return resolved_cwd
