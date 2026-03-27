"""Tracked-file snapshots and mutation gates for devflow."""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess

from clawops.common import load_json, sha256_hex, utc_now_ms, write_json
from clawops.typed_values import as_int, as_mapping


@dataclasses.dataclass(frozen=True, slots=True)
class GitSnapshot:
    """Hash snapshot of tracked files inside one workspace."""

    workspace_root: pathlib.Path
    git_root: pathlib.Path | None
    created_at_ms: int
    files: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe snapshot payload."""
        return {
            "workspace_root": self.workspace_root.as_posix(),
            "git_root": None if self.git_root is None else self.git_root.as_posix(),
            "created_at_ms": self.created_at_ms,
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "GitSnapshot":
        """Rehydrate one git snapshot from JSON."""
        mapping = as_mapping(payload, path="git snapshot")
        raw_git_root = mapping.get("git_root")
        raw_files = as_mapping(mapping.get("files", {}), path="git snapshot.files")
        return cls(
            workspace_root=pathlib.Path(str(mapping["workspace_root"])).expanduser().resolve(),
            git_root=(
                None
                if raw_git_root in {None, ""}
                else pathlib.Path(str(raw_git_root)).expanduser().resolve()
            ),
            created_at_ms=as_int(mapping.get("created_at_ms"), path="git snapshot.created_at_ms"),
            files={str(key): str(value) for key, value in raw_files.items()},
        )


@dataclasses.dataclass(frozen=True, slots=True)
class MutationCheck:
    """Result of comparing two git snapshots."""

    ok: bool
    mutated_paths: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe mutation-check payload."""
        return {
            "ok": self.ok,
            "mutated_paths": list(self.mutated_paths),
            "reason": self.reason,
        }


def _git_root(path: pathlib.Path) -> pathlib.Path | None:
    """Return the git root for *path* when present."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return pathlib.Path(result.stdout.strip()).expanduser().resolve()


def _tracked_files(workspace_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Return tracked files rooted at *workspace_root*."""
    git_root = _git_root(workspace_root)
    if git_root is None:
        return ()
    result = subprocess.run(
        ["git", "-C", str(workspace_root), "ls-files", "-z"],
        check=False,
        capture_output=True,
        text=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="ignore").strip() or "git ls-files failed"
        )
    files: list[pathlib.Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_path = raw_path.decode("utf-8")
        files.append(git_root / relative_path)
    return tuple(files)


def capture_git_snapshot(workspace_root: pathlib.Path) -> GitSnapshot:
    """Capture hashes for all tracked files in *workspace_root*."""
    resolved_workspace_root = workspace_root.expanduser().resolve()
    git_root = _git_root(resolved_workspace_root)
    files: dict[str, str] = {}
    if git_root is not None:
        for tracked_file in _tracked_files(resolved_workspace_root):
            relative_path = tracked_file.relative_to(git_root).as_posix()
            if tracked_file.exists():
                files[relative_path] = sha256_hex(tracked_file.read_bytes())
                continue
            files[relative_path] = "__deleted__"
    return GitSnapshot(
        workspace_root=resolved_workspace_root,
        git_root=git_root,
        created_at_ms=utc_now_ms(),
        files=files,
    )


def write_git_snapshot(path: pathlib.Path, snapshot: GitSnapshot) -> None:
    """Persist a git snapshot to JSON."""
    write_json(path, snapshot.to_dict())


def load_git_snapshot(path: pathlib.Path) -> GitSnapshot:
    """Load a git snapshot from JSON."""
    payload = as_mapping(load_json(path), path="git snapshot payload")
    return GitSnapshot.from_dict(dict(payload))


def check_tracked_mutations(before: GitSnapshot, after: GitSnapshot) -> MutationCheck:
    """Compare two snapshots and return tracked-file mutations."""
    if before.git_root is None or after.git_root is None:
        return MutationCheck(
            ok=False,
            mutated_paths=(),
            reason="workspace is not a git checkout",
        )
    mutated_paths: list[str] = []
    all_paths = sorted(set(before.files) | set(after.files))
    for relative_path in all_paths:
        if before.files.get(relative_path) != after.files.get(relative_path):
            mutated_paths.append(relative_path)
    if mutated_paths:
        return MutationCheck(
            ok=False,
            mutated_paths=tuple(mutated_paths),
            reason="tracked-file mutations detected",
        )
    return MutationCheck(ok=True, mutated_paths=(), reason="no tracked-file mutations detected")
