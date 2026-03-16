"""Run ACP worker sessions with preflight checks and durable summaries."""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import pathlib
import shutil
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Final

from clawops.common import dump_json, sha256_hex, write_json, write_text
from clawops.process_runner import run_command

DEFAULT_REPO_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]


class SessionLockError(RuntimeError):
    """Raised when another ACP session already owns the branch lock."""


@dataclasses.dataclass(slots=True, frozen=True)
class SessionSpec:
    """Immutable ACP session configuration."""

    backend: str
    branch: str
    prompt: str
    session_type: str
    repo_root: pathlib.Path
    worktree: pathlib.Path
    state_dir: pathlib.Path
    timeout_seconds: int

    @property
    def command(self) -> list[str]:
        """Return the `acpx` command line."""
        return ["acpx", self.backend, self.prompt]


@dataclasses.dataclass(slots=True, frozen=True)
class SessionSummary:
    """Machine-readable ACP session result."""

    ok: bool
    status: str
    message: str
    backend: str
    branch: str
    session_type: str
    repo_root: pathlib.Path
    worktree: pathlib.Path
    state_dir: pathlib.Path
    command: Sequence[str]
    started_at: str
    finished_at: str
    duration_ms: int
    returncode: int | None
    stdout_path: pathlib.Path
    stderr_path: pathlib.Path
    summary_path: pathlib.Path

    def to_dict(self) -> dict[str, object]:
        """Convert the session summary into JSON-safe primitives."""
        return {
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "backend": self.backend,
            "branch": self.branch,
            "session_type": self.session_type,
            "repo_root": str(self.repo_root),
            "worktree": str(self.worktree),
            "state_dir": str(self.state_dir),
            "command": list(self.command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "returncode": self.returncode,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "summary_path": str(self.summary_path),
        }


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _timestamp_token(value: datetime) -> str:
    """Return a stable timestamp token for session directories."""
    return value.strftime("%Y%m%dT%H%M%S%fZ")


def _timestamp_text(value: datetime) -> str:
    """Return an ISO-8601 UTC timestamp."""
    return value.isoformat().replace("+00:00", "Z")


def _default_worktree(repo_root: pathlib.Path, branch: str) -> pathlib.Path:
    """Return the default ACP worktree for a branch."""
    return repo_root / "repo" / "worktrees" / branch


def _lock_name(branch: str) -> str:
    """Return a deterministic lock filename for a branch."""
    return f"{sha256_hex(branch)[:16]}.lock"


def _git_stdout(worktree: pathlib.Path, *arguments: str) -> str | None:
    """Run a git query against the target worktree."""
    result = run_command(["git", "-C", str(worktree), *arguments], timeout_seconds=10)
    if not result.ok:
        return None
    return result.stdout.strip()


def _preflight_errors(spec: SessionSpec) -> list[str]:
    """Return a list of blocking ACP session errors."""
    errors: list[str] = []
    if not spec.branch.strip():
        errors.append("branch is required")
    if not spec.prompt.strip():
        errors.append("prompt must not be empty")
    if spec.timeout_seconds <= 0:
        errors.append("timeout_seconds must be positive")
    if shutil.which("acpx") is None:
        errors.append("acpx executable not found in PATH")
    if shutil.which("git") is None:
        errors.append("git executable not found in PATH")
    if not spec.repo_root.exists():
        errors.append(f"repo_root does not exist: {spec.repo_root}")
    if not spec.worktree.exists():
        errors.append(f"worktree does not exist: {spec.worktree}")
        return errors
    if not spec.worktree.is_dir():
        errors.append(f"worktree is not a directory: {spec.worktree}")
        return errors

    repo_area = (spec.repo_root / "repo").resolve()
    resolved_worktree = spec.worktree.resolve()
    if not resolved_worktree.is_relative_to(repo_area):
        errors.append(f"worktree must stay under {repo_area}")
        return errors

    actual_worktree = _git_stdout(spec.worktree, "rev-parse", "--show-toplevel")
    if actual_worktree is None:
        errors.append(f"worktree is not a git worktree: {spec.worktree}")
        return errors

    actual_branch = _git_stdout(spec.worktree, "symbolic-ref", "--short", "HEAD")
    if actual_branch is None:
        errors.append(f"unable to determine current branch for {spec.worktree}")
    elif actual_branch != spec.branch:
        errors.append(f"branch mismatch: expected {spec.branch}, found {actual_branch}")
    return errors


@contextmanager
def branch_lock(lock_path: pathlib.Path) -> Generator[None, None, None]:
    """Acquire a non-blocking exclusive lock for a branch."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SessionLockError(f"branch already locked: {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_session_artifacts(
    summary: SessionSummary,
    *,
    stdout_text: str,
    stderr_text: str,
) -> None:
    """Write logs and summary files for a session."""
    summary.summary_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(summary.stdout_path, stdout_text)
    write_text(summary.stderr_path, stderr_text)
    write_json(summary.summary_path, summary.to_dict())


def run_session(spec: SessionSpec) -> SessionSummary:
    """Execute an ACP session and persist its summary."""
    started = _utc_now()
    session_dir = spec.state_dir / spec.branch / spec.session_type / _timestamp_token(started)
    stdout_path = session_dir / "stdout.log"
    stderr_path = session_dir / "stderr.log"
    summary_path = session_dir / "summary.json"
    base_summary = SessionSummary(
        ok=False,
        status="pending",
        message="session created",
        backend=spec.backend,
        branch=spec.branch,
        session_type=spec.session_type,
        repo_root=spec.repo_root,
        worktree=spec.worktree,
        state_dir=spec.state_dir,
        command=spec.command,
        started_at=_timestamp_text(started),
        finished_at=_timestamp_text(started),
        duration_ms=0,
        returncode=None,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        summary_path=summary_path,
    )

    errors = _preflight_errors(spec)
    if errors:
        failed = dataclasses.replace(
            base_summary,
            status="preflight_failed",
            message="; ".join(errors),
            finished_at=_timestamp_text(_utc_now()),
        )
        _write_session_artifacts(failed, stdout_text="", stderr_text=failed.message)
        return failed

    lock_path = spec.state_dir / "locks" / _lock_name(spec.branch)
    try:
        with branch_lock(lock_path):
            result = run_command(
                spec.command,
                cwd=spec.worktree,
                timeout_seconds=spec.timeout_seconds,
            )
    except SessionLockError as exc:
        failed = dataclasses.replace(
            base_summary,
            status="lock_conflict",
            message=str(exc),
            finished_at=_timestamp_text(_utc_now()),
        )
        _write_session_artifacts(failed, stdout_text="", stderr_text=failed.message)
        return failed

    finished = _utc_now()
    if result.timed_out:
        summary = dataclasses.replace(
            base_summary,
            status="timed_out",
            message=f"acpx timed out after {spec.timeout_seconds}s",
            finished_at=_timestamp_text(finished),
            duration_ms=result.duration_ms,
        )
    elif result.failed_to_start:
        summary = dataclasses.replace(
            base_summary,
            status="failed_to_start",
            message=result.stderr or "acpx failed to start",
            finished_at=_timestamp_text(finished),
            duration_ms=result.duration_ms,
        )
    elif result.ok:
        summary = dataclasses.replace(
            base_summary,
            ok=True,
            status="succeeded",
            message="acpx session completed successfully",
            finished_at=_timestamp_text(finished),
            duration_ms=result.duration_ms,
            returncode=result.returncode,
        )
    else:
        summary = dataclasses.replace(
            base_summary,
            status="failed",
            message=f"acpx exited with status {result.returncode}",
            finished_at=_timestamp_text(finished),
            duration_ms=result.duration_ms,
            returncode=result.returncode,
        )

    _write_session_artifacts(summary, stdout_text=result.stdout, stderr_text=result.stderr)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for ACP sessions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("codex", "claude"))
    parser.add_argument("--branch", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--session-type", default=None)
    parser.add_argument("--repo-root", type=pathlib.Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--worktree", type=pathlib.Path, default=None)
    parser.add_argument("--state-dir", type=pathlib.Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a single ACP session."""
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    worktree = (
        args.worktree.resolve()
        if args.worktree is not None
        else _default_worktree(repo_root, args.branch).resolve()
    )
    state_dir = (
        args.state_dir.resolve() if args.state_dir is not None else (repo_root / ".runs" / "acp")
    )
    spec = SessionSpec(
        backend=args.backend,
        branch=args.branch,
        prompt=args.prompt,
        session_type=args.session_type or args.backend,
        repo_root=repo_root,
        worktree=worktree,
        state_dir=state_dir,
        timeout_seconds=args.timeout_seconds,
    )
    summary = run_session(spec)
    print(dump_json(summary.to_dict()).rstrip())
    if summary.ok:
        return 0
    if summary.returncode is not None:
        return int(summary.returncode)
    return 1
