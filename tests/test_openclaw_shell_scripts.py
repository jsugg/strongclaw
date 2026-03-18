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


def _write_fake_varlock(bin_dir: pathlib.Path, log_path: pathlib.Path | None = None) -> None:
    target = bin_dir / "varlock"
    if log_path is None:
        body = "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n"
    else:
        body = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'printf "%s\\n" "$*" >> "{log_path}"\n'
            "exit 0\n"
        )
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)


def _write_fake_docker(
    bin_dir: pathlib.Path,
    *,
    compose_available: bool = True,
    backend_ready: bool = True,
    log_path: pathlib.Path | None = None,
) -> None:
    target = bin_dir / "docker"
    lines = ["#!/bin/bash", "set -euo pipefail"]
    if log_path is not None:
        lines.append(f'printf "%s\\n" "$*" >> "{log_path}"')
    lines.extend(
        [
            'if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then',
            f"  exit {0 if compose_available else 1}",
            "fi",
            'if [[ "${1:-}" == "info" ]]; then',
            f"  exit {0 if backend_ready else 1}",
            "fi",
            "exit 0",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    target.chmod(0o755)


def _write_recording_script(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/bin/bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)


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
    assert "bootstrap.sh" in result.stderr


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


def test_install_host_services_renders_repo_local_systemd_user_units(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    home_dir = tmp_path / "home"
    systemd_dir = home_dir / ".config" / "systemd" / "user"
    bin_dir = _build_tool_path(tmp_path, ["dirname"])
    (bin_dir / "uname").write_text("#!/usr/bin/env bash\nprintf 'Linux\\n'\n", encoding="utf-8")
    (bin_dir / "uname").chmod(0o755)
    env = os.environ | {
        "HOME": str(home_dir),
        "SYSTEMD_DIR": str(systemd_dir),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/install_host_services.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    gateway_unit = (systemd_dir / "openclaw-gateway.service").read_text(encoding="utf-8")
    sidecars_unit = (systemd_dir / "openclaw-sidecars.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={repo_root}" in gateway_unit
    assert (
        f"ExecStart=/bin/bash -lc '{repo_root}/scripts/ops/launch_gateway_with_varlock.sh'"
        in gateway_unit
    )
    assert (
        f"ExecStart=/bin/bash -lc '{repo_root}/scripts/ops/launch_sidecars_with_varlock.sh'"
        in sidecars_unit
    )
    assert "/srv/openclaw" not in gateway_unit
    assert "systemctl --user daemon-reload" in result.stdout
    assert "systemctl --user enable --now openclaw-gateway.service" in result.stdout


def test_install_host_services_dispatches_to_host_specific_renderer(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    home_dir = tmp_path / "home"
    bin_dir = _build_tool_path(tmp_path, ["dirname"])
    (bin_dir / "uname").write_text("#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (bin_dir / "uname").chmod(0o755)
    env = os.environ | {
        "HOME": str(home_dir),
        "LAUNCHD_DIR": str(home_dir / "Library" / "LaunchAgents"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/install_host_services.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Rendered launchd plists into" in result.stdout
    assert "launchctl bootstrap gui/" in result.stdout


def test_install_host_services_activates_systemd_user_units_when_requested(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    home_dir = tmp_path / "home"
    systemd_dir = home_dir / ".config" / "systemd" / "user"
    log_path = tmp_path / "systemctl.log"
    bin_dir = _build_tool_path(tmp_path, ["dirname"])
    (bin_dir / "uname").write_text("#!/usr/bin/env bash\nprintf 'Linux\\n'\n", encoding="utf-8")
    (bin_dir / "uname").chmod(0o755)
    _write_fake_docker(bin_dir)
    _write_recording_script(
        bin_dir / "systemctl",
        f'printf "%s\\n" "$*" >> "{log_path}"\n',
    )
    env = os.environ | {
        "HOME": str(home_dir),
        "SYSTEMD_DIR": str(systemd_dir),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/install_host_services.sh"), "--activate"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    logged = log_path.read_text(encoding="utf-8")
    assert "--user daemon-reload" in logged
    assert "--user enable --now openclaw-sidecars.service" in logged
    assert "--user enable --now openclaw-gateway.service" in logged
    assert "Activated user systemd services" in result.stdout


def test_install_host_services_bootstraps_launchd_services_when_requested(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    home_dir = tmp_path / "home"
    launchd_dir = home_dir / "Library" / "LaunchAgents"
    log_path = tmp_path / "launchctl.log"
    bin_dir = _build_tool_path(tmp_path, ["dirname"])
    (bin_dir / "uname").write_text("#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (bin_dir / "uname").chmod(0o755)
    _write_fake_docker(bin_dir)
    _write_recording_script(
        bin_dir / "id",
        'if [[ "${1:-}" == "-u" ]]; then\n  printf "501\\n"\nelse\n  /usr/bin/id "$@"\nfi\n',
    )
    _write_recording_script(
        bin_dir / "launchctl",
        f'printf "%s\\n" "$*" >> "{log_path}"\n'
        'if [[ "${1:-}" == "print" ]]; then\n  exit 0\nfi\n',
    )
    env = os.environ | {
        "HOME": str(home_dir),
        "LAUNCHD_DIR": str(launchd_dir),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/install_host_services.sh"), "--activate"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    logged = log_path.read_text(encoding="utf-8")
    assert "print gui/501/ai.openclaw.gateway" in logged
    assert f"bootout gui/501 {launchd_dir / 'ai.openclaw.gateway.plist'}" in logged
    assert f"bootstrap gui/501 {launchd_dir / 'ai.openclaw.sidecars.plist'}" in logged
    assert "Activated launchd services for gui/501" in result.stdout


def test_validate_varlock_env_requires_repo_local_env_file(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    env = os.environ | {"VARLOCK_ENV_DIR": str(env_dir)}

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/validate_varlock_env.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert f"Varlock local env contract not found at {env_dir / '.env.local'}" in result.stderr


def test_validate_varlock_env_uses_directory_entrypoint_for_varlock_load(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    (env_dir / ".env.local").write_text("OPENCLAW_GATEWAY_TOKEN=test-token\n", encoding="utf-8")
    log_path = tmp_path / "varlock.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_varlock(bin_dir, log_path)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VARLOCK_ENV_DIR": str(env_dir),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/validate_varlock_env.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [f"load --path {env_dir}"]
    assert f"Validated Varlock env contract at {env_dir / '.env.local'}" in result.stdout


def test_create_openclawsvc_requires_root_for_linux_branch(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "id"])
    (bin_dir / "uname").write_text("#!/usr/bin/env bash\nprintf 'Linux\\n'\n", encoding="utf-8")
    (bin_dir / "uname").chmod(0o755)
    env = os.environ | {"HOME": str(tmp_path / "home"), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    username = "openclawsvc_test_nonroot"

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/create_openclawsvc.sh"),
            username,
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Run with sudo to create the Linux runtime user" in result.stderr


def test_docker_runtime_reuses_existing_docker_cli_without_installing_fallback(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "sudo.log"
    _write_fake_docker(bin_dir, compose_available=True, backend_ready=False)
    _write_recording_script(bin_dir / "sudo", f'printf "%s\\n" "$*" >> "{log_path}"\n')
    env = os.environ | {"PATH": str(bin_dir)}

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                f'source "{repo_root / "scripts/lib/docker_runtime.sh"}"; '
                "ensure_docker_compatible_runtime linux; "
                'printf "%s\\n" "$DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP"'
            ),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0"
    assert not log_path.exists()


def test_docker_runtime_installs_docker_only_when_no_backend_is_detected(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "apt-get.log"
    _write_recording_script(bin_dir / "sudo", 'exec "$@"\n')
    _write_recording_script(
        bin_dir / "apt-get",
        f'printf "%s\\n" "$*" >> "{log_path}"\n'
        'if [[ "${1:-}" == "install" ]]; then\n'
        f'  /bin/cat > "{bin_dir / "docker"}" <<\'EOF\'\n'
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "info" ]]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
        "EOF\n"
        f'  /bin/chmod 755 "{bin_dir / "docker"}"\n'
        "fi\n",
    )
    env = os.environ | {"PATH": str(bin_dir)}

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                f'source "{repo_root / "scripts/lib/docker_runtime.sh"}"; '
                "ensure_docker_compatible_runtime linux; "
                'printf "%s\\n" "$DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP"'
            ),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "1"
    assert "install -y docker.io docker-compose-plugin" in log_path.read_text(encoding="utf-8")


def test_docker_runtime_refuses_to_install_docker_over_orbstack(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "brew.log"
    _write_recording_script(bin_dir / "orb", "exit 0\n")
    _write_recording_script(bin_dir / "brew", f'printf "%s\\n" "$*" >> "{log_path}"\n')
    env = os.environ | {"PATH": str(bin_dir)}

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'source "{repo_root / "scripts/lib/docker_runtime.sh"}"; ensure_docker_compatible_runtime darwin',
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "detected OrbStack" in result.stderr
    assert "will not install Docker over an existing alternative runtime" in result.stderr
    assert not log_path.exists()


def test_install_script_composes_bootstrap_service_activation_and_verification(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "install.log"
    bootstrap_script = tmp_path / "bootstrap.sh"
    validate_script = tmp_path / "validate_varlock_env.sh"
    install_script = tmp_path / "install_host_services.sh"
    verify_script = tmp_path / "verify_baseline.sh"
    _write_recording_script(
        bootstrap_script,
        f'printf "bootstrap profile=%s\\n" "${{OPENCLAW_CONFIG_PROFILE:-default}}" >> "{log_path}"\n',
    )
    _write_recording_script(
        validate_script,
        f'printf "validate\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        install_script,
        f'printf "install %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        verify_script,
        f'printf "verify\\n" >> "{log_path}"\n',
    )
    env = os.environ | {
        "BOOTSTRAP_SCRIPT": str(bootstrap_script),
        "VALIDATE_VARLOCK_ENV_SCRIPT": str(validate_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
        "VERIFY_BASELINE_SCRIPT": str(verify_script),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/install.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "bootstrap profile=default",
        "validate",
        "install --activate",
        "verify",
    ]


def test_install_script_skip_bootstrap_supports_post_bootstrap_followups(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "install-profile.log"
    bootstrap_script = tmp_path / "bootstrap.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    doctor_script = tmp_path / "doctor_host.sh"
    install_script = tmp_path / "install_host_services.sh"
    _write_recording_script(
        bootstrap_script,
        f'printf "bootstrap profile=%s\\n" "${{OPENCLAW_CONFIG_PROFILE:-default}}" >> "{log_path}"\n',
    )
    _write_recording_script(
        render_script,
        f'printf "render %s profile=%s\\n" "$*" "${{OPENCLAW_CONFIG_PROFILE:-default}}" >> "{log_path}"\n',
    )
    _write_recording_script(
        doctor_script,
        f'printf "doctor\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        install_script,
        f'printf "install %s\\n" "$*" >> "{log_path}"\n',
    )
    env = os.environ | {
        "BOOTSTRAP_SCRIPT": str(bootstrap_script),
        "RENDER_OPENCLAW_CONFIG_SCRIPT": str(render_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/install.sh"),
            "--profile",
            "acp",
            "--skip-bootstrap",
            "--no-activate-services",
            "--no-verify",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "render --profile acp profile=acp",
        "doctor",
        "install ",
    ]


def test_preflight_requires_homebrew_for_darwin(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname"])
    (bin_dir / "uname").write_text("#!/bin/bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (bin_dir / "uname").chmod(0o755)
    env = os.environ | {"PATH": str(bin_dir)}

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/preflight.sh")],
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
    _write_fake_varlock(bin_dir)
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
