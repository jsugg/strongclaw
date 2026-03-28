"""Black-box CLI coverage for the public devflow surface."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

from tests.utils.helpers.devflow import (
    init_git_repo,
    install_fake_devflow_backends,
    write_strongclaw_shaped_repo,
)


def test_devflow_cli_exercises_plan_run_status_resume_and_audit(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_strongclaw_shaped_repo(repo_root)
    init_git_repo(repo_root)
    bin_dir = tmp_path / "bin"
    install_fake_devflow_backends(bin_dir)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["PYTHONPATH"] = str((pathlib.Path(__file__).resolve().parents[4] / "src").resolve())

    plan = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "devflow",
            "plan",
            "--project-root",
            str(repo_root),
            "--goal",
            "cli smoke",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "devflow",
            "run",
            "--project-root",
            str(repo_root),
            "--goal",
            "cli smoke",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    run_payload = json.loads(run.stdout)
    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "devflow",
            "status",
            "--project-root",
            str(repo_root),
            "--run-id",
            run_payload["run_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    resume = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "devflow",
            "resume",
            "--project-root",
            str(repo_root),
            "--run-id",
            run_payload["run_id"],
            "--approved-by",
            "tester",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    audit = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "devflow",
            "audit",
            "--project-root",
            str(repo_root),
            "--run-id",
            run_payload["run_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(plan.stdout)["goal"] == "cli smoke"
    assert run.returncode == 1
    assert json.loads(status.stdout)["run"]["status"] == "failed"
    assert json.loads(resume.stdout)["ok"] is True
    assert json.loads(audit.stdout)["ok"] is True
