"""Tests for repo/worktree operator tooling."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from clawops.repo_tools import repo_main, worktree_main


def _run_git(*args: str, cwd: pathlib.Path | None = None) -> None:
    subprocess.run(
        ["git", *args],
        check=True,
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
    )


def _init_repo_contract(tmp_path: pathlib.Path) -> pathlib.Path:
    repo_root = tmp_path / "strongclaw"
    upstream = repo_root / "repo" / "upstream"
    worktrees = repo_root / "repo" / "worktrees"
    upstream.mkdir(parents=True)
    worktrees.mkdir(parents=True)
    _run_git("init", "-b", "main", str(upstream))
    _run_git("-C", str(upstream), "config", "user.name", "Test User")
    _run_git("-C", str(upstream), "config", "user.email", "test@example.com")
    (upstream / "README.md").write_text("# upstream\n", encoding="utf-8")
    _run_git("-C", str(upstream), "add", "README.md")
    _run_git("-C", str(upstream), "commit", "-m", "init")
    return repo_root


def test_repo_doctor_reports_healthy_repo_contract(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = _init_repo_contract(tmp_path)

    exit_code = repo_main(["--repo-root", str(repo_root), "doctor"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["checks"]["managedWorktrees"] == 0


def test_worktree_new_list_and_prune_manage_the_repo_contract(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = _init_repo_contract(tmp_path)

    create_exit = worktree_main(
        ["--repo-root", str(repo_root), "new", "--branch", "feature/review-lane"]
    )
    create_payload = json.loads(capsys.readouterr().out)
    created_path = pathlib.Path(create_payload["created"]["path"])

    assert create_exit == 0
    assert created_path.exists()
    assert create_payload["created"]["managed"] is True

    list_exit = worktree_main(["--repo-root", str(repo_root), "list"])
    list_payload = json.loads(capsys.readouterr().out)
    assert list_exit == 0
    assert any(
        entry["branch"] == "feature/review-lane" and entry["managed"] is True
        for entry in list_payload["worktrees"]
    )

    shutil.rmtree(created_path)
    prune_exit = worktree_main(["--repo-root", str(repo_root), "prune"])
    prune_payload = json.loads(capsys.readouterr().out)

    assert prune_exit == 0
    assert not any(entry["path"] == created_path.as_posix() for entry in prune_payload["worktrees"])
