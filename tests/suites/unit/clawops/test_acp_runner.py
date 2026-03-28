"""Tests for ACP session orchestration."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import argparse
import fcntl
import json
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any, Protocol, cast

import pytest

import clawops.acp_runner as acp_runner
from clawops.acp_runner import main as acp_runner_main
from clawops.acp_runner import parse_args
from tests.utils.helpers.cli import (
    PathPrepender,
    require_system_executable,
    symlink_executable,
    write_fake_acpx,
    write_status_script,
)


class _SessionSpecLike(Protocol):
    """Subset of the runner session spec used by this test."""

    lock_identity: str


_lock_name = cast(
    Callable[[str], str],
    cast(Any, acp_runner)._lock_name,
)
_resolve_session_spec = cast(
    Callable[[argparse.Namespace], _SessionSpecLike],
    cast(Any, acp_runner)._resolve_session_spec,
)


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
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)

    write_fake_acpx(cli_bin_dir)
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)

    state_dir = repo_root / ".runs" / "acp"
    exit_code = acp_runner_main(
        [
            "--backend",
            "codex",
            "--branch",
            "main",
            "--session-type",
            "developer",
            "--project-root",
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
    assert summary_stdout["auth_mode"] == "subscription"

    summary_path = _session_summary_path(state_dir)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["branch"] == "main"
    assert summary["session_type"] == "developer"
    assert summary["workspace_root"] == str(worktree.resolve())
    assert summary["command"] == [
        "acpx",
        "--approve-reads",
        "--format",
        "text",
        "codex",
        "exec",
        "Summarize the worktree",
    ]
    assert summary["requested_permissions_mode"] is None
    assert summary["applied_permissions_mode"] == "approve-reads"
    assert summary["requested_output_format"] == "text"
    assert summary["backend_profile"] is None
    assert summary["acpx_command"] == summary["command"]

    stdout_path = pathlib.Path(summary["stdout_path"])
    stderr_path = pathlib.Path(summary["stderr_path"])
    audit_path = pathlib.Path(summary["audit_path"])
    assert (
        "fake-acpx --approve-reads --format text codex exec Summarize the worktree"
        in stdout_path.read_text(encoding="utf-8")
    )
    assert "stderr from fake-acpx" in stderr_path.read_text(encoding="utf-8")
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_payload["credential_state"] == "ready"
    assert audit_payload["applied_permissions_mode"] == "approve-reads"
    assert audit_payload["requested_output_format"] == "text"


def test_acp_runner_persists_requested_adapter_contract(
    tmp_path: pathlib.Path,
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)

    write_fake_acpx(cli_bin_dir, stdout_text='{"ok": true}')
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)

    state_dir = repo_root / ".runs" / "acp"
    exit_code = acp_runner_main(
        [
            "--backend",
            "codex",
            "--backend-profile",
            "gpt-5",
            "--permissions-mode",
            "approve-all",
            "--output-format",
            "json",
            "--branch",
            "main",
            "--session-type",
            "developer",
            "--project-root",
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
    assert summary_stdout["requested_permissions_mode"] == "approve-all"
    assert summary_stdout["applied_permissions_mode"] == "approve-all"
    assert summary_stdout["requested_output_format"] == "json"
    assert summary_stdout["backend_profile"] == "gpt-5"
    assert summary_stdout["parsed_output_format"] == "json"
    assert summary_stdout["acpx_command"] == [
        "acpx",
        "--approve-all",
        "--format",
        "json",
        "--json-strict",
        "--model",
        "gpt-5",
        "codex",
        "exec",
        "Summarize the worktree",
    ]

    summary_path = _session_summary_path(state_dir)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    audit_payload = json.loads(pathlib.Path(summary["audit_path"]).read_text(encoding="utf-8"))
    structured_output = json.loads(
        pathlib.Path(summary["structured_output_path"]).read_text(encoding="utf-8")
    )
    assert audit_payload["backend_profile"] == "gpt-5"
    assert audit_payload["acpx_command"] == summary["acpx_command"]
    assert structured_output == {"events": [], "format": "json", "payload": {"ok": True}}


def test_acp_runner_fails_preflight_when_acpx_is_missing(
    tmp_path: pathlib.Path,
    cli_bin_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)
    symlink_executable(cli_bin_dir, require_system_executable("git"))
    write_status_script(cli_bin_dir, "claude", stdout_text='{"status":"authenticated"}')
    monkeypatch.setenv("PATH", cli_bin_dir.as_posix())

    state_dir = repo_root / ".runs" / "acp"
    exit_code = acp_runner_main(
        [
            "--backend",
            "claude",
            "--branch",
            "main",
            "--session-type",
            "reviewer",
            "--project-root",
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


def test_acp_runner_detects_lock_conflicts(
    tmp_path: pathlib.Path,
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo-root"
    worktree = repo_root / "repo" / "upstream"
    worktree.mkdir(parents=True)
    _init_git_worktree(worktree)

    write_fake_acpx(cli_bin_dir)
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)

    state_dir = repo_root / ".runs" / "acp"
    args = parse_args(
        [
            "--backend",
            "codex",
            "--branch",
            "main",
            "--session-type",
            "developer",
            "--project-root",
            str(repo_root),
            "--worktree",
            str(worktree),
            "--state-dir",
            str(state_dir),
            "--prompt",
            "Summarize the worktree",
        ]
    )
    spec = _resolve_session_spec(args)
    lock_path = state_dir / "locks" / _lock_name(spec.lock_identity)
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
                "developer",
                "--project-root",
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
        assert "session already locked" in summary["message"]


def test_acp_runner_supports_non_git_local_dir_without_branch(
    tmp_path: pathlib.Path,
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    workspace = project_root / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    write_fake_acpx(cli_bin_dir)
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)

    exit_code = acp_runner_main(
        [
            "--backend",
            "codex",
            "--project-root",
            str(project_root),
            "--workspace",
            str(workspace),
            "--workspace-kind",
            "local_dir",
            "--role",
            "developer",
            "--lane",
            "feature-a",
            "--prompt",
            "Summarize the local workspace",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    summary = json.loads(captured.out)
    assert summary["workspace_kind"] == "local_dir"
    assert summary["branch"] is None
    assert summary["role"] == "developer"
    assert summary["lane"] == "feature-a"
