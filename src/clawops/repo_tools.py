"""Operator tooling for the repo/upstream and repo/worktrees contract."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
from typing import Any

from clawops.common import ResultSummary
from clawops.process_runner import run_command


def _resolve_repo_root(repo_root: pathlib.Path) -> pathlib.Path:
    """Resolve the repo root used by the workspace contract."""
    return repo_root.expanduser().resolve()


def _upstream_repo(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the managed upstream repository path."""
    return repo_root / "repo" / "upstream"


def _worktrees_root(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the managed worktree root."""
    return repo_root / "repo" / "worktrees"


def _git_available() -> bool:
    """Return True when git is available on PATH."""
    return shutil.which("git") is not None


def _git_text(*arguments: str) -> tuple[bool, str]:
    """Run a git command and return success plus output/error text."""
    result = run_command(["git", *arguments], timeout_seconds=30)
    output = (
        result.stdout.strip() if result.ok else (result.stderr.strip() or result.stdout.strip())
    )
    return result.ok, output


def _parse_worktree_list(text: str) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` output."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current is not None:
                entries.append(current)
                current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current is not None:
                entries.append(current)
            current = {
                "path": value,
                "branch": None,
                "head": None,
                "locked": False,
                "prunable": False,
                "detached": False,
            }
            continue
        if current is None:
            continue
        if key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "locked":
            current["locked"] = True
        elif key == "prunable":
            current["prunable"] = True
        elif key == "detached":
            current["detached"] = True
    if current is not None:
        entries.append(current)
    return entries


def list_worktrees(repo_root: pathlib.Path) -> dict[str, Any]:
    """List git worktrees managed by the repo contract."""
    resolved_root = _resolve_repo_root(repo_root)
    upstream_repo = _upstream_repo(resolved_root)
    managed_root = _worktrees_root(resolved_root)
    if not _git_available():
        raise RuntimeError("git executable not found in PATH")
    ok, output = _git_text("-C", str(upstream_repo), "worktree", "list", "--porcelain")
    if not ok:
        raise RuntimeError(output or f"unable to list git worktrees under {upstream_repo}")
    entries = _parse_worktree_list(output)
    for entry in entries:
        entry_path = pathlib.Path(str(entry["path"])).resolve()
        entry["managed"] = entry_path.is_relative_to(managed_root)
    return {
        "ok": True,
        "repo_root": resolved_root.as_posix(),
        "upstream": upstream_repo.as_posix(),
        "worktrees": entries,
    }


def repo_doctor(repo_root: pathlib.Path, *, branch: str | None) -> dict[str, Any]:
    """Validate the repo/upstream and repo/worktrees contract."""
    resolved_root = _resolve_repo_root(repo_root)
    upstream_repo = _upstream_repo(resolved_root)
    managed_root = _worktrees_root(resolved_root)
    errors: list[str] = []
    checks: dict[str, Any] = {
        "repoRoot": resolved_root.as_posix(),
        "upstream": upstream_repo.as_posix(),
        "worktreesRoot": managed_root.as_posix(),
        "gitAvailable": _git_available(),
        "upstreamExists": upstream_repo.exists(),
        "worktreesRootExists": managed_root.exists(),
    }

    if not checks["gitAvailable"]:
        errors.append("git executable not found in PATH")
    if not upstream_repo.exists():
        errors.append(f"missing upstream repository: {upstream_repo}")
    elif not upstream_repo.is_dir():
        errors.append(f"upstream repository is not a directory: {upstream_repo}")
    if not managed_root.exists():
        errors.append(f"missing worktrees root: {managed_root}")
    elif not managed_root.is_dir():
        errors.append(f"worktrees root is not a directory: {managed_root}")

    worktree_payload: dict[str, Any] | None = None
    if not errors:
        try:
            worktree_payload = list_worktrees(resolved_root)
        except RuntimeError as exc:
            errors.append(str(exc))
        else:
            checks["managedWorktrees"] = sum(
                1 for entry in worktree_payload["worktrees"] if bool(entry.get("managed"))
            )
            if branch is not None:
                expected_path = (managed_root / branch).resolve()
                matching = [
                    entry
                    for entry in worktree_payload["worktrees"]
                    if pathlib.Path(str(entry["path"])).resolve() == expected_path
                ]
                checks["expectedBranch"] = branch
                checks["expectedWorktree"] = expected_path.as_posix()
                if not matching:
                    errors.append(f"missing managed worktree for branch {branch}: {expected_path}")
                elif matching[0].get("branch") != branch:
                    errors.append(
                        f"branch mismatch for {expected_path}: expected {branch}, found {matching[0].get('branch')}"
                    )

    payload: dict[str, Any] = {"ok": not errors, "checks": checks}
    if errors:
        payload["errors"] = errors
    if worktree_payload is not None:
        payload["worktrees"] = worktree_payload["worktrees"]
    return payload


def create_worktree(
    repo_root: pathlib.Path,
    *,
    branch: str,
    start_point: str,
    path: pathlib.Path | None,
) -> dict[str, Any]:
    """Create or attach a managed worktree for a branch."""
    resolved_root = _resolve_repo_root(repo_root)
    upstream_repo = _upstream_repo(resolved_root)
    managed_root = _worktrees_root(resolved_root)
    managed_root.mkdir(parents=True, exist_ok=True)
    destination = (path or (managed_root / branch)).expanduser().resolve()
    if not destination.parent.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_relative_to(managed_root):
        raise ValueError(f"worktree path must stay under {managed_root}")
    if destination.exists():
        raise FileExistsError(destination)

    branch_exists, _ = _git_text(
        "-C", str(upstream_repo), "show-ref", "--verify", f"refs/heads/{branch}"
    )
    command = ["git", "-C", str(upstream_repo), "worktree", "add"]
    if branch_exists:
        command.extend([destination.as_posix(), branch])
    else:
        command.extend(["-b", branch, destination.as_posix(), start_point])
    result = run_command(command, timeout_seconds=60)
    if not result.ok:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "git worktree add failed"
        )
    list_payload = list_worktrees(resolved_root)
    created = next(
        entry
        for entry in list_payload["worktrees"]
        if pathlib.Path(str(entry["path"])).resolve() == destination
    )
    return {
        "ok": True,
        "branch": branch,
        "created": created,
        "stdout": result.stdout.strip(),
    }


def prune_worktrees(repo_root: pathlib.Path) -> dict[str, Any]:
    """Prune stale git worktree admin records and return the remaining set."""
    resolved_root = _resolve_repo_root(repo_root)
    before = list_worktrees(resolved_root)
    upstream_repo = _upstream_repo(resolved_root)
    result = run_command(
        ["git", "-C", str(upstream_repo), "worktree", "prune", "--expire", "now"],
        timeout_seconds=30,
    )
    if not result.ok:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "git worktree prune failed"
        )
    after = list_worktrees(resolved_root)
    return {
        "ok": True,
        "beforeCount": len(before["worktrees"]),
        "afterCount": len(after["worktrees"]),
        "worktrees": after["worktrees"],
    }


def repo_parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the `clawops repo` command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Validate the repo/upstream + repo/worktrees layout.")
    doctor.add_argument("--branch")
    return parser.parse_args(argv)


def worktree_parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the `clawops worktree` command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List managed git worktrees.")
    new = sub.add_parser("new", help="Create a new managed git worktree.")
    new.add_argument("--branch", required=True)
    new.add_argument("--start-point", default="HEAD")
    new.add_argument("--path", type=pathlib.Path)
    sub.add_parser("prune", help="Prune stale git worktree admin state.")
    return parser.parse_args(argv)


def repo_main(argv: list[str] | None = None) -> int:
    """Run the `clawops repo` CLI."""
    args = repo_parse_args(argv)
    if args.command != "doctor":
        result = ResultSummary(False, f"unsupported repo command: {args.command}")
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 2
    payload = repo_doctor(args.repo_root, branch=args.branch)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def worktree_main(argv: list[str] | None = None) -> int:
    """Run the `clawops worktree` CLI."""
    args = worktree_parse_args(argv)
    try:
        if args.command == "list":
            payload = list_worktrees(args.repo_root)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "new":
            payload = create_worktree(
                args.repo_root,
                branch=args.branch,
                start_point=args.start_point,
                path=args.path,
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "prune":
            payload = prune_worktrees(args.repo_root)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
    except (FileExistsError, RuntimeError, ValueError) as exc:
        result = ResultSummary(False, str(exc))
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 1
    result = ResultSummary(False, f"unsupported worktree command: {args.command}")
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 2
