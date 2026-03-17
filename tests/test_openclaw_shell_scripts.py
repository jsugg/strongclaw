"""Behavior tests for shell scripts that depend on OpenClaw."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile


def _build_tool_path(tmp_path: pathlib.Path, tool_names: list[str]) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in tool_names:
        source = shutil.which(name)
        if source is None:
            raise AssertionError(f"required test tool not found: {name}")
        (bin_dir / name).symlink_to(source)
    return bin_dir


def _write_fake_openclaw(bin_dir: pathlib.Path) -> None:
    target = bin_dir / "openclaw"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == "--version" ]]; then\n'
        "  printf 'openclaw 2026.3.13\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$*" == "config validate" ]]; then\n'
        "  printf 'config ok\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'fake-openclaw %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_fake_acpx(bin_dir: pathlib.Path) -> None:
    target = bin_dir / "acpx"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == "--version" ]]; then\n'
        "  printf 'acpx 0.3.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'fake-acpx %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_fake_clawops(bin_dir: pathlib.Path) -> None:
    target = bin_dir / "clawops"
    target.write_text(
        "#!/usr/bin/env bash\n" "set -euo pipefail\n" "printf 'fake-clawops %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_fake_pytest(bin_dir: pathlib.Path) -> None:
    target = bin_dir / "pytest"
    target.write_text(
        "#!/usr/bin/env bash\n" "set -euo pipefail\n" "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_clawops_launcher(bin_dir: pathlib.Path, repo_root: pathlib.Path) -> None:
    target = bin_dir / "clawops"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'export PYTHONPATH="{repo_root / "src"}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'exec "{sys.executable}" -m clawops "$@"\n',
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_fake_qmd(home_dir: pathlib.Path) -> pathlib.Path:
    target = home_dir / ".bun" / "bin" / "qmd"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "#!/usr/bin/env bash\n" "set -euo pipefail\n" "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
    return target


def test_verify_baseline_fails_fast_when_openclaw_is_missing(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname"])
    env = os.environ | {"PATH": str(bin_dir), "HOME": str(tmp_path / "home")}

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/verify_baseline.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "ERROR: Baseline verification runs OpenClaw diagnostics and audits." in result.stderr
    assert "bootstrap_" in result.stderr


def test_verify_baseline_fails_fast_when_qmd_is_missing(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_openclaw(bin_dir)
    _write_fake_pytest(bin_dir)
    home_dir = tmp_path / "home"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/verify_baseline.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "ERROR: Baseline verification requires the QMD semantic memory backend" in result.stderr
    assert "bootstrap_qmd.sh" in result.stderr


def test_render_openclaw_config_enables_qmd_with_local_paths(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env = os.environ | {
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(repo_root / "src"),
        "OPENCLAW_USER_TIMEZONE": "UTC",
    }
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/render_openclaw_config.sh")],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    output_path = pathlib.Path(env["HOME"]) / ".openclaw" / "openclaw.json"
    rendered = output_path.read_text(encoding="utf-8")
    assert '"backend": "qmd"' in rendered
    assert f'"command": "{pathlib.Path(env["HOME"]).resolve().as_posix()}/.bun/bin/qmd"' in rendered
    assert f'"path": "{(repo_root / "platform/docs").resolve().as_posix()}"' in rendered
    assert '"pattern": "memory.md"' in rendered
    assert (
        f'"workspace": "{(repo_root / "platform/workspace/admin").resolve().as_posix()}"'
        in rendered
    )
    assert '"userTimezone": "UTC"' in rendered


def test_render_openclaw_config_supports_profiles_and_exec_approvals_output(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    approvals_output = tmp_path / "home" / ".openclaw" / "exec-approvals.json"
    env = os.environ | {
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(repo_root / "src"),
        "OPENCLAW_USER_TIMEZONE": "UTC",
    }
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/render_openclaw_config.sh"),
            "--profile",
            "acp",
            "--exec-approvals-output",
            str(approvals_output),
        ],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    rendered = json.loads(
        ((tmp_path / "home") / ".openclaw" / "openclaw.json").read_text(encoding="utf-8")
    )
    approvals = json.loads(approvals_output.read_text(encoding="utf-8"))

    coder = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "coder-acp-codex")
    assert coder["runtime"]["acp"]["cwd"] == f"{repo_root.as_posix()}/repo/upstream"
    assert approvals["rules"][0]["match"]["cwdPrefixes"] == [
        repo_root.as_posix(),
        f"{repo_root.as_posix()}/repo/upstream",
    ]


def test_verify_baseline_runs_from_non_repo_cwd_when_dependencies_exist(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_openclaw(bin_dir)
    _write_fake_pytest(bin_dir)
    home_dir = tmp_path / "home"
    _write_fake_qmd(home_dir)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
    }
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/verify_baseline.sh")],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "== OpenClaw doctor ==" in result.stdout
    assert "== OpenClaw memory status ==" in result.stdout
    assert "== OpenClaw memory search ==" in result.stdout
    assert "== Harness smoke ==" in result.stdout
    assert "passed=2 total=2" in result.stdout
    assert "passed=3 total=3" in result.stdout


def test_preflight_macos_requires_homebrew(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname"])
    env = os.environ | {"PATH": str(bin_dir)}

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/preflight_macos.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Homebrew is required for macOS bootstrap." in result.stderr


def test_doctor_host_validates_installed_tools_and_rendered_config(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "jq"])
    _write_fake_openclaw(bin_dir)
    _write_fake_acpx(bin_dir)
    home_dir = tmp_path / "home"
    _write_fake_qmd(home_dir)
    config_path = home_dir / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"gateway":{"bind":"loopback"}}\n', encoding="utf-8")
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
    }
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/doctor_host.sh")],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "== OpenClaw version ==" in result.stdout
    assert "openclaw 2026.3.13" in result.stdout
    assert "== ACPX version ==" in result.stdout
    assert "acpx 0.3.0" in result.stdout
    assert "config ok" in result.stdout
    assert "validated " in result.stdout


def test_backup_create_warns_and_falls_back_without_openclaw(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname", "date", "mkdir", "tar", "gzip"])
    home = tmp_path / "home"
    claw_home = home / ".openclaw"
    claw_home.mkdir(parents=True)
    (claw_home / "state.txt").write_text("ok\n", encoding="utf-8")
    env = os.environ | {"PATH": str(bin_dir), "HOME": str(home)}

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/recovery/backup_create.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (
        "WARNING: OpenClaw backup CLI unavailable; falling back to a tar archive." in result.stderr
    )
    assert "Can't add archive to itself" not in result.stderr
    archive = pathlib.Path(result.stdout.strip())
    assert archive.exists()
    with tarfile.open(archive, "r:gz") as tar_handle:
        assert any(member.name.endswith("state.txt") for member in tar_handle.getmembers())


def test_backup_verify_warns_and_uses_tar_when_openclaw_is_missing(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname", "ls", "head", "tar", "gzip"])
    home = tmp_path / "home"
    backup_dir = home / ".openclaw" / "backups"
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "openclaw-test.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("ok\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar_handle:
        tar_handle.add(payload, arcname="payload.txt")
    env = os.environ | {"PATH": str(bin_dir), "HOME": str(home)}

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/recovery/backup_verify.sh"), "latest"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (
        "WARNING: OpenClaw backup verification unavailable; falling back to tar verification."
        in result.stderr
    )
    assert f"Verified {archive}" in result.stdout


def test_restore_openclaw_runs_from_non_repo_cwd(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname", "ls", "head", "mkdir", "tar", "gzip"])
    home = tmp_path / "home"
    backup_dir = home / ".openclaw" / "backups"
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "openclaw-test.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("ok\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar_handle:
        tar_handle.add(payload, arcname="payload.txt")
    restore_dir = tmp_path / "restored"
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home)}

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/recovery/restore_openclaw.sh"),
            str(archive),
            str(restore_dir),
        ],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert f"Verified {archive}" in result.stdout
    assert f"Restored into {restore_dir}" in result.stdout
    assert (restore_dir / "payload.txt").read_text(encoding="utf-8") == "ok\n"


def test_daily_healthcheck_workflow_runs_documented_openclaw_commands(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname"])
    _write_fake_openclaw(bin_dir)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHONPATH": str(repo_root / "src"),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "workflow",
            "--workflow",
            str(repo_root / "platform/configs/workflows/daily_healthcheck.yaml"),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "openclaw doctor\tok\texit=0" in result.stdout
    assert "security audit\tok\texit=0" in result.stdout


def test_code_review_workflow_runs_from_non_repo_cwd_using_declared_base_dir(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    env = os.environ | {"PYTHONPATH": str(repo_root / "src")}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "clawops",
            "workflow",
            "--workflow",
            str(repo_root / "platform/configs/workflows/code_review.yaml"),
            "--dry-run",
        ],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "policy preflight\tok\trequire_approval" in result.stdout
    assert "dry context pack\tok\tdry-run context pack" in result.stdout


def test_run_workflow_helper_sets_repo_base_dir_from_non_repo_cwd(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname"])
    _write_clawops_launcher(bin_dir, repo_root)
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/ops/run_workflow.sh"),
            str(repo_root / "platform/configs/workflows/code_review.yaml"),
            "--dry-run",
        ],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "policy preflight\tok\trequire_approval" in result.stdout


def test_run_workflow_resolves_repo_relative_paths_from_non_repo_cwd(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_clawops(bin_dir)
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/ops/run_workflow.sh"),
            "platform/configs/workflows/daily_healthcheck.yaml",
            "--dry-run",
        ],
        cwd=outside_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (
        "fake-clawops workflow --workflow "
        f"{repo_root / 'platform/configs/workflows/daily_healthcheck.yaml'} "
        f"--base-dir {repo_root} --dry-run"
    ) in result.stdout
