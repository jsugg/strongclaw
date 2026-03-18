"""Tests for ACP session orchestration."""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import shutil
import subprocess

from clawops.acp_runner import _lock_name
from clawops.acp_runner import main as acp_runner_main


def _write_fake_acpx(bin_dir: pathlib.Path, *, exit_code: int = 0) -> None:
    target = bin_dir / "acpx"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf 'fake-acpx %s\\n' \"$*\"\n"
        "printf 'stderr from fake-acpx\\n' >&2\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _init_git_worktree(worktree: pathlib.Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main", str(worktree)],
        check=True,
        capture_output=True,
        text=True,
    )


def _session_summary_path(state_dir: pathlib.Path) -> pathlib.Path:
    return next(state_dir.glob("**/summary.json"))


def test_acp_runner_writes_summary_and_logs(
    tmp_path: pathlib.Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_acpx(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    state_dir = repo_root / ".runs" / "acp"
    exit_code = acp_runner_main(
        [
            "--backend",
            "codex",
            "--branch",
            "main",
            "--session-type",
            "coder",
            "--repo-root",
            str(repo_root),
            "--worktree",
            str(worktree),
            "--state-dir",
            str(state_dir),
            "--prompt",
            "Summarize the worktree",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    summary_stdout = json.loads(captured.out)
    assert summary_stdout["status"] == "succeeded"

    summary_path = _session_summary_path(state_dir)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["branch"] == "main"
    assert summary["session_type"] == "coder"
    assert summary["worktree"] == str(worktree.resolve())
    assert summary["command"] == ["acpx", "codex", "Summarize the worktree"]

    stdout_path = pathlib.Path(summary["stdout_path"])
    stderr_path = pathlib.Path(summary["stderr_path"])
    assert "fake-acpx codex Summarize the worktree" in stdout_path.read_text(encoding="utf-8")
    assert "stderr from fake-acpx" in stderr_path.read_text(encoding="utf-8")


def test_acp_runner_fails_preflight_when_acpx_is_missing(
    tmp_path: pathlib.Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)
    git_bin = tmp_path / "bin"
    git_bin.mkdir()
    git_path = shutil.which("git")
    if git_path is None:
        raise AssertionError("git is required for this test")
    (git_bin / "git").symlink_to(git_path)
    monkeypatch.setenv("PATH", git_bin.as_posix())

    state_dir = repo_root / ".runs" / "acp"
    exit_code = acp_runner_main(
        [
            "--backend",
            "claude",
            "--branch",
            "main",
            "--session-type",
            "reviewer",
            "--repo-root",
            str(repo_root),
            "--worktree",
            str(worktree),
            "--state-dir",
            str(state_dir),
            "--prompt",
            "Review the diff",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    summary = json.loads(captured.out)
    assert summary["status"] == "preflight_failed"
    assert "acpx executable not found in PATH" in summary["message"]


def test_acp_runner_detects_branch_lock_conflicts(
    tmp_path: pathlib.Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_acpx(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    state_dir = repo_root / ".runs" / "acp"
    lock_path = state_dir / "locks" / _lock_name("main")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        exit_code = acp_runner_main(
            [
                "--backend",
                "codex",
                "--branch",
                "main",
                "--session-type",
                "coder",
                "--repo-root",
                str(repo_root),
                "--worktree",
                str(worktree),
                "--state-dir",
                str(state_dir),
                "--prompt",
                "Summarize the worktree",
            ]
        )
        captured = capsys.readouterr()
        assert exit_code == 1
        summary = json.loads(captured.out)
        assert summary["status"] == "lock_conflict"
        assert "branch already locked" in summary["message"]
