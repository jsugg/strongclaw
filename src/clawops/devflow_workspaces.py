"""Stage workspace planning and synchronization for devflow."""

from __future__ import annotations

import dataclasses
import pathlib
import shutil
import subprocess
from typing import Final

from clawops.devflow_roles import WorkspaceMode
from clawops.orchestration import ProjectDescriptor, WorkspaceDescriptor

PRIMARY_MUTABLE_MODE: Final[WorkspaceMode] = "mutable_primary"
TEST_MUTABLE_MODE: Final[WorkspaceMode] = "mutable_test"
VERIFY_ONLY_MODE: Final[WorkspaceMode] = "verify_only"
READ_ONLY_MODE: Final[WorkspaceMode] = "read_only"


@dataclasses.dataclass(frozen=True, slots=True)
class PlannedWorkspace:
    """Resolved workspace for one devflow stage."""

    stage_name: str
    workspace_mode: WorkspaceMode
    root: pathlib.Path
    descriptor: WorkspaceDescriptor

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe workspace payload."""
        return {
            "stage_name": self.stage_name,
            "workspace_mode": self.workspace_mode,
            "root": self.root.as_posix(),
            "descriptor": {
                "project_id": self.descriptor.project_id,
                "workspace_id": self.descriptor.workspace_id,
                "kind": self.descriptor.kind,
                "root": self.descriptor.root.as_posix(),
                "working_directory": self.descriptor.working_directory.as_posix(),
                "branch": self.descriptor.branch,
            },
        }


def _git_root(path: pathlib.Path) -> pathlib.Path | None:
    """Return the git root for a path when available."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return pathlib.Path(result.stdout.strip()).expanduser().resolve()


def _clear_workspace_content(path: pathlib.Path) -> None:
    """Remove synced workspace content while preserving git metadata."""
    for child in path.iterdir():
        if child.name in {".git", ".clawops"}:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink()


def _copy_tree_contents(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Synchronize workspace contents, excluding git metadata."""
    _clear_workspace_content(destination)
    for child in source.iterdir():
        if child.name in {".git", ".clawops"}:
            continue
        target = destination / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target, symlinks=True)
            continue
        shutil.copy2(child, target, follow_symlinks=False)


def _ensure_git_worktree(
    repo_root: pathlib.Path,
    destination: pathlib.Path,
    *,
    ref: str,
) -> pathlib.Path:
    """Create or reuse a detached git worktree rooted at *destination*."""
    if destination.exists():
        git_root = _git_root(destination)
        if git_root is None:
            raise ValueError(f"workspace exists but is not a git worktree: {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(destination), ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "git worktree add failed"
        )
    return destination


def _ensure_copied_workspace(source_root: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    """Create or resync a copied workspace."""
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"workspace path is not a directory: {destination}")
    else:
        destination.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(source_root, destination)
    return destination


class DevflowWorkspacePlanner:
    """Prepare isolated per-stage workspaces for one devflow run."""

    def __init__(self, *, repo_root: pathlib.Path, run_root: pathlib.Path) -> None:
        self.repo_root = repo_root.expanduser().resolve()
        self.run_root = run_root.expanduser().resolve()
        self.workspaces_root = self.run_root / "workspaces"
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self.project = ProjectDescriptor.resolve(
            self.repo_root, trusted_roots=(self.workspaces_root,)
        )
        self._git_repo_root = _git_root(self.repo_root)

    def _workspace_path(self, stage_name: str, workspace_mode: WorkspaceMode) -> pathlib.Path:
        """Return the stable on-disk workspace path for one stage."""
        return self.workspaces_root / f"{stage_name}-{workspace_mode}"

    def prepare(
        self,
        *,
        stage_name: str,
        workspace_mode: WorkspaceMode,
        source_root: pathlib.Path,
    ) -> PlannedWorkspace:
        """Create or resolve one stage workspace."""
        resolved_source_root = source_root.expanduser().resolve()
        if workspace_mode == PRIMARY_MUTABLE_MODE:
            root = self._prepare_primary(stage_name=stage_name, source_root=resolved_source_root)
        elif workspace_mode in {TEST_MUTABLE_MODE, VERIFY_ONLY_MODE, READ_ONLY_MODE}:
            root = self._prepare_synced(
                stage_name=stage_name,
                workspace_mode=workspace_mode,
                source_root=resolved_source_root,
            )
        else:
            raise ValueError(f"unsupported workspace mode: {workspace_mode}")
        descriptor_kind = "git_worktree" if _git_root(root) is not None else "local_dir"
        descriptor = WorkspaceDescriptor.resolve(
            self.project,
            kind=descriptor_kind,
            path=root,
        )
        return PlannedWorkspace(
            stage_name=stage_name,
            workspace_mode=workspace_mode,
            root=root,
            descriptor=descriptor,
        )

    def _prepare_primary(self, *, stage_name: str, source_root: pathlib.Path) -> pathlib.Path:
        """Return the mutable primary workspace."""
        destination = self._workspace_path(stage_name, PRIMARY_MUTABLE_MODE)
        if self._git_repo_root is not None:
            root = _ensure_git_worktree(self._git_repo_root, destination, ref="HEAD")
            if source_root != root:
                _copy_tree_contents(source_root, root)
            return root
        return _ensure_copied_workspace(source_root, destination)

    def _prepare_synced(
        self,
        *,
        stage_name: str,
        workspace_mode: WorkspaceMode,
        source_root: pathlib.Path,
    ) -> pathlib.Path:
        """Return a synced non-primary workspace."""
        destination = self._workspace_path(stage_name, workspace_mode)
        if self._git_repo_root is not None:
            root = _ensure_git_worktree(self._git_repo_root, destination, ref="HEAD")
            _copy_tree_contents(source_root, root)
            return root
        return _ensure_copied_workspace(source_root, destination)
