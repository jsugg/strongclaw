"""Behavior tests for shell scripts that depend on OpenClaw."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile

from clawops.app_paths import strongclaw_qmd_install_dir


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


def _write_fake_openclaw_model_manager(bin_dir: pathlib.Path, state_dir: pathlib.Path) -> None:
    target = bin_dir / "openclaw"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'STATE_DIR="{state_dir}"\n'
        'mkdir -p "$STATE_DIR"\n'
        'if [[ "${1:-}" == "agents" && "${2:-}" == "list" && "${3:-}" == "--json" ]]; then\n'
        '  printf \'[{"id":"admin"},{"id":"reader"}]\\n\'\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "models" && "${2:-}" == "status" && "${3:-}" == "--help" ]]; then\n'
        "  printf 'models status help\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" != "models" ]]; then\n'
        "  printf 'unexpected openclaw args: %s\\n' \"$*\" >&2\n"
        "  exit 1\n"
        "fi\n"
        "shift\n"
        'agent_id="admin"\n'
        'if [[ "${1:-}" == "--agent" ]]; then\n'
        '  agent_id="${2:-}"\n'
        "  shift 2\n"
        "fi\n"
        'primary_file="$STATE_DIR/${agent_id}.primary"\n'
        'fallback_file="$STATE_DIR/${agent_id}.fallbacks"\n'
        'case "${1:-}" in\n'
        "  list)\n"
        '    primary="openai-codex/gpt-5.4"\n'
        '    if [[ -f "$primary_file" ]]; then\n'
        '      primary="$(cat "$primary_file")"\n'
        "    fi\n"
        '    available="false"\n'
        '    case "$primary" in\n'
        '      openai/gpt-5.4|anthropic/claude-opus-4-6|zai/glm-5|ollama/*) available="true" ;;\n'
        "    esac\n"
        '    printf \'{"models":[{"key":"%s","available":%s}]}\\n\' "$primary" "$available"\n'
        "    ;;\n"
        "  status)\n"
        '    if [[ "${2:-}" == "--agent" ]]; then\n'
        '      agent_id="${3:-}"\n'
        "      shift 3\n"
        "    else\n"
        "      shift\n"
        "    fi\n"
        '    primary="openai-codex/gpt-5.4"\n'
        '    if [[ -f "$primary_file" ]]; then\n'
        '      primary="$(cat "$primary_file")"\n'
        "    fi\n"
        '    case "$primary" in\n'
        "      openai/gpt-5.4|anthropic/claude-opus-4-6|zai/glm-5|ollama/*) exit 0 ;;\n"
        '      *) printf "unhealthy model: %s\\n" "$primary" >&2; exit 1 ;;\n'
        "    esac\n"
        "    ;;\n"
        "  set)\n"
        '    printf "%s" "${2:-}" > "$primary_file"\n'
        "    ;;\n"
        "  fallbacks)\n"
        '    case "${2:-}" in\n'
        "      clear)\n"
        '        : > "$fallback_file"\n'
        "        ;;\n"
        "      add)\n"
        '        printf "%s\\n" "${3:-}" >> "$fallback_file"\n'
        "        ;;\n"
        "      *)\n"
        '        printf "unexpected fallback args: %s\\n" "$*" >&2\n'
        "        exit 1\n"
        "        ;;\n"
        "    esac\n"
        "    ;;\n"
        "  configure)\n"
        '    printf "unexpected configure invocation\\n" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "  *)\n"
        '    printf "unexpected models args: %s\\n" "$*" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n",
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
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'fake-clawops %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def _write_fake_clawops_with_hypermemory_status(
    bin_dir: pathlib.Path,
    *,
    status_payload: dict[str, object],
    verify_payload: dict[str, object] | None = None,
    log_path: pathlib.Path | None = None,
) -> None:
    target = bin_dir / "clawops"
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    if log_path is not None:
        lines.append(f'printf "%s\\n" "$*" >> "{log_path}"')
    lines.extend(
        [
            'if [[ "${1:-}" == "hypermemory" && "${2:-}" == "status" ]]; then',
            f"  printf '%s\\n' '{json.dumps(status_payload, sort_keys=True)}'",
            "  exit 0",
            "fi",
            'if [[ "${1:-}" == "hypermemory" && "${2:-}" == "verify" ]]; then',
            f"  printf '%s\\n' '{json.dumps(verify_payload or {'ok': True}, sort_keys=True)}'",
            "  exit 0",
            "fi",
            "printf 'fake-clawops %s\\n' \"$*\"",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    target.chmod(0o755)


def _write_fake_uv(bin_dir: pathlib.Path) -> None:
    target = bin_dir / "uv"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "run" ]]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
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
        "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
    return target


def _write_fake_varlock(bin_dir: pathlib.Path, log_path: pathlib.Path | None = None) -> None:
    target = bin_dir / "varlock"
    if log_path is None:
        body = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "--version" ]]; then\n'
            "  printf 'varlock 0.5.0\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        )
    else:
        body = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "--version" ]]; then\n'
            "  printf 'varlock 0.5.0\\n'\n"
            "  exit 0\n"
            "fi\n"
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
    env_log_path: pathlib.Path | None = None,
) -> None:
    target = bin_dir / "docker"
    lines = ["#!/bin/bash", "set -euo pipefail"]
    if log_path is not None:
        lines.append(f'printf "%s\\n" "$*" >> "{log_path}"')
    if env_log_path is not None:
        lines.append(
            f'printf "STRONGCLAW_COMPOSE_STATE_DIR=%s\\n" "${{STRONGCLAW_COMPOSE_STATE_DIR:-}}" >> "{env_log_path}"'
        )
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


def _write_fake_qdrant_curl(
    path: pathlib.Path, *, payload: dict[str, object], delete_log: pathlib.Path
) -> None:
    payload_json = json.dumps(payload)
    path.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                "set -euo pipefail",
                'method="GET"',
                'url=""',
                "while [[ $# -gt 0 ]]; do",
                '  case "$1" in',
                "    -X)",
                '      method="${2:-GET}"',
                "      shift 2",
                "      ;;",
                "    -f|-s|-S|-fsS)",
                "      shift",
                "      ;;",
                "    *)",
                '      url="$1"',
                "      shift",
                "      ;;",
                "  esac",
                "done",
                'if [[ "$method" == "DELETE" ]]; then',
                f'  printf "%s\\n" "$url" >> "{delete_log}"',
                "  exit 0",
                "fi",
                f"cat <<'EOF'\n{payload_json}\nEOF",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_valid_varlock_env(path: pathlib.Path) -> None:
    path.write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "OPENCLAW_VERSION=2026.3.13",
                "VARLOCK_SECRET_BACKEND=local",
                "OPENCLAW_GATEWAY_TOKEN=test-gateway-token-1234567890",
                "OPENCLAW_CONTROL_USER=test-user",
                "OPENCLAW_STATE_DIR=~/.openclaw",
                "LITELLM_MASTER_KEY=test-master-key-1234567890",
                "LITELLM_DB_PASSWORD=test-db-password-1234",
                "HYPERMEMORY_EMBEDDING_BASE_URL=http://127.0.0.1:4000/v1",
                "HYPERMEMORY_QDRANT_URL=http://127.0.0.1:6333",
                "WHATSAPP_SESSION_DIR=~/.openclaw/channels/whatsapp",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


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


def test_verify_baseline_requires_a_rendered_openclaw_config(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_openclaw(bin_dir)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
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
    assert "ERROR: Rendered OpenClaw config not found" in result.stderr


def test_bootstrap_qmd_reinstalls_broken_launcher_from_published_package(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    home_dir = tmp_path / "home"
    qmd_bin = home_dir / ".bun" / "bin" / "qmd"
    qmd_install_dir = strongclaw_qmd_install_dir(home_dir=home_dir)
    qmd_dist = qmd_install_dir / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"
    qmd_bin.parent.mkdir(parents=True, exist_ok=True)
    qmd_bin.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'broken qmd\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    qmd_bin.chmod(0o755)
    install_log = tmp_path / "npm.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_recording_script(
        bin_dir / "npm",
        f'printf "%s\\n" "$*" >> "{install_log}"\n'
        f'mkdir -p "{qmd_dist.parent}"\n'
        f"cat > \"{qmd_dist}\" <<'EOF'\n"
        "console.log('qmd help');\n"
        "EOF\n"
        f'chmod 644 "{qmd_dist}"\n',
    )
    _write_recording_script(
        bin_dir / "node",
        f'if [[ "${{1:-}}" == "{qmd_dist}" && "${{2:-}}" == "status" ]]; then\n'
        "  printf 'qmd status\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    env = os.environ | {
        "HOME": str(home_dir),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/bootstrap_qmd.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "launcher is unhealthy; reinstalling" in result.stdout
    assert install_log.read_text(encoding="utf-8").splitlines() == [
        f"install --prefix {qmd_install_dir} --no-fund --no-audit @tobilu/qmd@2.0.1"
    ]
    assert "QMD installed at:" in result.stdout
    assert qmd_bin.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")


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
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/render_openclaw_config.sh"),
            "--profile",
            "openclaw-qmd",
        ],
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
    assert '"name": "repo-root-markdown"' in rendered
    assert '"pattern": "*.md"' in rendered
    assert (
        f'"workspace": "{(repo_root / "platform/workspace/admin").resolve().as_posix()}"'
        in rendered
    )
    assert '"userTimezone": "UTC"' in rendered


def test_render_openclaw_config_defaults_to_hypermemory_profile(tmp_path: pathlib.Path) -> None:
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
    rendered = json.loads(
        ((tmp_path / "home") / ".openclaw" / "openclaw.json").read_text(encoding="utf-8")
    )

    assert rendered["plugins"]["slots"] == {
        "contextEngine": "lossless-claw",
        "memory": "strongclaw-hypermemory",
    }
    plugin_config = rendered["plugins"]["entries"]["strongclaw-hypermemory"]["config"]
    assert (
        plugin_config["configPath"]
        == f"{repo_root.as_posix()}/platform/configs/memory/hypermemory.yaml"
    )


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
    _write_fake_uv(bin_dir)
    _write_fake_clawops(bin_dir)
    verify_models_script = tmp_path / "verify_openclaw_models.sh"
    _write_recording_script(verify_models_script, "printf 'verify-models %s\\n' \"$*\"\n")
    home_dir = tmp_path / "home"
    config_path = home_dir / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"gateway":{"bind":"loopback"}}\n', encoding="utf-8")
    _write_fake_qmd(home_dir)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "CLAWOPS_PREFER_PATH": "1",
        "VERIFY_OPENCLAW_MODELS_SCRIPT": str(verify_models_script),
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
    assert "verify-models --check-only" in result.stdout
    assert "== Harness smoke ==" in result.stdout
    assert "fake-clawops harness --suite" in result.stdout
    assert "security_regressions.yaml" in result.stdout
    assert "policy_regressions.yaml" in result.stdout


def test_verify_baseline_uses_varlock_contract_when_available_off_path(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_openclaw(bin_dir)
    _write_fake_uv(bin_dir)
    _write_fake_clawops(bin_dir)
    verify_models_script = tmp_path / "verify_openclaw_models.sh"
    _write_recording_script(verify_models_script, "printf 'verify-models %s\\n' \"$*\"\n")
    home_dir = tmp_path / "home"
    config_path = home_dir / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"gateway":{"bind":"loopback"}}\n', encoding="utf-8")
    _write_fake_qmd(home_dir)
    varlock_log = tmp_path / "varlock.log"
    install_dir = home_dir / ".config" / "varlock" / "bin"
    install_dir.mkdir(parents=True)
    _write_fake_varlock(install_dir, varlock_log)
    xdg_config_home = home_dir / ".config"
    env = os.environ | {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home_dir),
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "CLAWOPS_PREFER_PATH": "1",
        "VERIFY_OPENCLAW_MODELS_SCRIPT": str(verify_models_script),
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
    assert varlock_log.read_text(encoding="utf-8").splitlines()[:5] == [
        f"run --path {repo_root / 'platform/configs/varlock'} -- openclaw doctor --non-interactive",
        f"run --path {repo_root / 'platform/configs/varlock'} -- openclaw security audit --deep",
        f"run --path {repo_root / 'platform/configs/varlock'} -- openclaw secrets audit --check",
        f"run --path {repo_root / 'platform/configs/varlock'} -- openclaw memory status --deep",
        f"run --path {repo_root / 'platform/configs/varlock'} -- openclaw memory search --query ClawOps --max-results 1",
    ]


def test_verify_baseline_uses_hypermemory_status_json_to_gate_hypermemory_verification(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "jq"])
    _write_fake_openclaw(bin_dir)
    _write_fake_uv(bin_dir)
    clawops_log = tmp_path / "clawops.log"
    _write_fake_clawops_with_hypermemory_status(
        bin_dir,
        status_payload={"backendActive": "qdrant_sparse_dense_hybrid"},
        log_path=clawops_log,
    )
    verify_models_script = tmp_path / "verify_openclaw_models.sh"
    _write_recording_script(verify_models_script, "printf 'verify-models %s\\n' \"$*\"\n")
    home_dir = tmp_path / "home"
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir(parents=True, exist_ok=True)
    hypermemory_config = tmp_path / "hypermemory.sqlite.yaml"
    hypermemory_config.write_text(
        "backend:\n  active: qdrant_sparse_dense_hybrid\n", encoding="utf-8"
    )
    openclaw_dir.joinpath("openclaw.json").write_text(
        json.dumps(
            {
                "gateway": {"bind": "loopback"},
                "plugins": {
                    "slots": {"memory": "strongclaw-hypermemory"},
                    "entries": {
                        "strongclaw-hypermemory": {
                            "config": {"configPath": hypermemory_config.as_posix()}
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "CLAWOPS_PREFER_PATH": "1",
        "VERIFY_OPENCLAW_MODELS_SCRIPT": str(verify_models_script),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/verify_baseline.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert '"backendActive": "qdrant_sparse_dense_hybrid"' in result.stdout
    assert clawops_log.read_text(encoding="utf-8").splitlines()[:2] == [
        f"hypermemory status --config {hypermemory_config} --json",
        f"hypermemory verify --config {hypermemory_config} --json",
    ]


def test_verify_baseline_skips_hypermemory_verification_for_dense_only_hypermemory_backend(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "jq"])
    _write_fake_openclaw(bin_dir)
    _write_fake_uv(bin_dir)
    clawops_log = tmp_path / "clawops.log"
    _write_fake_clawops_with_hypermemory_status(
        bin_dir,
        status_payload={"backendActive": "qdrant_dense_hybrid"},
        log_path=clawops_log,
    )
    verify_models_script = tmp_path / "verify_openclaw_models.sh"
    _write_recording_script(verify_models_script, "printf 'verify-models %s\\n' \"$*\"\n")
    home_dir = tmp_path / "home"
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir(parents=True, exist_ok=True)
    hypermemory_config = tmp_path / "hypermemory.sqlite.yaml"
    hypermemory_config.write_text("backend:\n  active: qdrant_dense_hybrid\n", encoding="utf-8")
    openclaw_dir.joinpath("openclaw.json").write_text(
        json.dumps(
            {
                "gateway": {"bind": "loopback"},
                "plugins": {
                    "slots": {"memory": "strongclaw-hypermemory"},
                    "entries": {
                        "strongclaw-hypermemory": {
                            "config": {"configPath": hypermemory_config.as_posix()}
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "CLAWOPS_PREFER_PATH": "1",
        "VERIFY_OPENCLAW_MODELS_SCRIPT": str(verify_models_script),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/verify_baseline.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert clawops_log.read_text(encoding="utf-8").splitlines()[0] == (
        f"hypermemory status --config {hypermemory_config} --json"
    )
    assert "hypermemory verify" not in clawops_log.read_text(encoding="utf-8")


def test_configure_openclaw_model_auth_fails_check_only_without_usable_models(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_dir = tmp_path / "state"
    _write_fake_openclaw_model_manager(bin_dir, state_dir)
    home_dir = tmp_path / "home"
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env.local").write_text("OPENCLAW_GATEWAY_TOKEN=test-token\n", encoding="utf-8")
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(env_dir / ".env.local"),
        "OPENCLAW_VARLOCK_ENV_PATH": str(env_dir),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_openclaw_model_auth.sh"),
            "--check-only",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "does not have a usable assistant model yet" in result.stderr
    assert "OPENCLAW_DEFAULT_MODEL" in result.stderr


def test_configure_openclaw_model_auth_applies_env_driven_model_chain(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_dir = tmp_path / "state"
    _write_fake_openclaw_model_manager(bin_dir, state_dir)
    home_dir = tmp_path / "home"
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    (openclaw_dir / "openclaw.json").write_text("{}", encoding="utf-8")
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    (env_dir / ".env.local").write_text(
        "\n".join(
            [
                "OPENCLAW_GATEWAY_TOKEN=test-token",
                "OPENAI_API_KEY=test-openai-key",
                "ANTHROPIC_API_KEY=test-anthropic-key",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(env_dir / ".env.local"),
        "OPENCLAW_VARLOCK_ENV_PATH": str(env_dir),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/configure_openclaw_model_auth.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (state_dir / "admin.primary").read_text(encoding="utf-8") == "openai/gpt-5.4"
    assert (state_dir / "reader.primary").read_text(encoding="utf-8") == "openai/gpt-5.4"
    assert (state_dir / "admin.fallbacks").read_text(encoding="utf-8").splitlines() == [
        "anthropic/claude-opus-4-6"
    ]


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
    _write_valid_varlock_env(env_dir / ".env.local")
    log_path = tmp_path / "varlock.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_varlock(bin_dir, log_path)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VARLOCK_ENV_DIR": str(env_dir),
        "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
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


def test_validate_varlock_env_uses_installed_varlock_when_not_on_path(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    _write_valid_varlock_env(env_dir / ".env.local")
    log_path = tmp_path / "varlock.log"
    home_dir = tmp_path / "home"
    install_dir = home_dir / ".config" / "varlock" / "bin"
    install_dir.mkdir(parents=True)
    _write_fake_varlock(install_dir, log_path)
    xdg_config_home = home_dir / ".config"
    env = os.environ | {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home_dir),
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "VARLOCK_ENV_DIR": str(env_dir),
        "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
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


def test_configure_varlock_env_creates_local_contract_and_replaces_placeholders(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    template_path = env_dir / ".env.local.example"
    template_path.write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "OPENCLAW_VERSION=2026.3.13",
                "OPENCLAW_GATEWAY_TOKEN=replace-with-long-random-token",
                "OPENCLAW_CONTROL_USER=openclawsvc",
                "OPENCLAW_STATE_DIR=~/.openclaw",
                "LITELLM_MASTER_KEY=replace-with-random-key",
                "LITELLM_DB_PASSWORD=replace-with-db-password",
                "WHATSAPP_SESSION_DIR=~/.openclaw/channels/whatsapp",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "varlock.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_varlock(bin_dir, log_path)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VARLOCK_ENV_DIR": str(env_dir),
        "VARLOCK_ENV_TEMPLATE": str(template_path),
        "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--non-interactive",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    local_env = (env_dir / ".env.local").read_text(encoding="utf-8")
    assert "replace-with-long-random-token" not in local_env
    assert "replace-with-random-key" not in local_env
    assert "replace-with-db-password" not in local_env
    assert "OPENCLAW_CONTROL_USER=" in local_env
    assert log_path.read_text(encoding="utf-8").splitlines() == [f"load --path {env_dir}"]


def test_configure_varlock_env_check_only_rejects_placeholder_required_values(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    (env_dir / ".env.local").write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "OPENCLAW_VERSION=2026.3.13",
                "OPENCLAW_GATEWAY_TOKEN=replace-with-long-random-token",
                "OPENCLAW_CONTROL_USER=openclawsvc",
                "OPENCLAW_STATE_DIR=~/.openclaw",
                "LITELLM_MASTER_KEY=replace-with-random-key",
                "LITELLM_DB_PASSWORD=replace-with-db-password",
                "WHATSAPP_SESSION_DIR=~/.openclaw/channels/whatsapp",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ | {"VARLOCK_ENV_DIR": str(env_dir)}

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--check-only",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "blank or still uses a placeholder" in result.stderr


def test_configure_varlock_env_check_only_accepts_plugin_backed_provider_backend(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    (env_dir / ".env.local").write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "OPENCLAW_VERSION=2026.3.13",
                "VARLOCK_SECRET_BACKEND=google-secret-manager",
                "VARLOCK_SECRET_BACKEND_AUTH=gcp-service-account-json",
                "OPENCLAW_GATEWAY_TOKEN=test-gateway-token-1234567890",
                "OPENCLAW_CONTROL_USER=test-user",
                "OPENCLAW_STATE_DIR=~/.openclaw",
                "LITELLM_MASTER_KEY=test-master-key-1234567890",
                "LITELLM_DB_PASSWORD=test-db-password-1234",
                "HYPERMEMORY_EMBEDDING_BASE_URL=http://127.0.0.1:4000/v1",
                "HYPERMEMORY_QDRANT_URL=http://127.0.0.1:6333",
                "OPENCLAW_DEFAULT_MODEL=openai/gpt-5.4",
                "WHATSAPP_SESSION_DIR=~/.openclaw/channels/whatsapp",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (env_dir / ".env.plugins").write_text(
        "\n".join(
            [
                "# @plugin(@varlock/google-secret-manager-plugin)",
                "# @initGsm(projectId=my-project, credentials=$GCP_SA_KEY)",
                "# ---",
                "# @type=gcpServiceAccountJson",
                'GCP_SA_KEY={"type":"service_account","project_id":"my-project","private_key":"-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----\\n","client_email":"svc@example.com"}',
                "OPENAI_API_KEY=gsm()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "varlock.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_varlock(bin_dir, log_path)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VARLOCK_ENV_DIR": str(env_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(env_dir / ".env.local"),
        "VARLOCK_PLUGIN_ENV_FILE": str(env_dir / ".env.plugins"),
        "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--check-only",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [f"load --path {env_dir}"]


def test_configure_varlock_env_check_only_rejects_stale_plugin_overlay_for_local_backend(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    _write_valid_varlock_env(env_dir / ".env.local")
    (env_dir / ".env.plugins").write_text("OPENAI_API_KEY=gsm()\n", encoding="utf-8")
    env = os.environ | {
        "VARLOCK_ENV_DIR": str(env_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(env_dir / ".env.local"),
        "VARLOCK_PLUGIN_ENV_FILE": str(env_dir / ".env.plugins"),
        "OPENCLAW_CONFIG_PROFILE": "openclaw-default",
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--check-only",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "VARLOCK_SECRET_BACKEND=local" in result.stderr


def test_configure_varlock_env_check_only_requires_hypermemory_embedding_model(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    _write_valid_varlock_env(env_dir / ".env.local")
    env = os.environ | {
        "OPENCLAW_CONFIG_PROFILE": "hypermemory",
        "VARLOCK_ENV_DIR": str(env_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(env_dir / ".env.local"),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--check-only",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "HYPERMEMORY_EMBEDDING_MODEL is required" in result.stderr


def test_configure_varlock_env_check_only_accepts_hypermemory_embedding_model(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    local_env = env_dir / ".env.local"
    _write_valid_varlock_env(local_env)
    local_env.write_text(
        local_env.read_text(encoding="utf-8")
        + "HYPERMEMORY_EMBEDDING_MODEL=openai/text-embedding-3-small\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "varlock.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_varlock(bin_dir, log_path)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "OPENCLAW_CONFIG_PROFILE": "hypermemory",
        "VARLOCK_ENV_DIR": str(env_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(local_env),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--check-only",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [f"load --path {env_dir}"]


def test_configure_varlock_env_non_interactive_uses_local_ollama_embedding_model(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_dir = tmp_path / "varlock"
    env_dir.mkdir()
    local_env = env_dir / ".env.local"
    _write_valid_varlock_env(local_env)
    log_path = tmp_path / "varlock.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_varlock(bin_dir, log_path)
    _write_recording_script(
        bin_dir / "ollama",
        (
            "cat <<'EOF'\n"
            "NAME                       ID              SIZE      MODIFIED      \n"
            "nomic-embed-text:latest    0a109f422b47    274 MB    20 months ago\n"
            "llama3:latest              a6990ed6be41    4.7 GB    22 months ago\n"
            "EOF\n"
        ),
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "OPENCLAW_CONFIG_PROFILE": "hypermemory",
        "VARLOCK_ENV_DIR": str(env_dir),
        "VARLOCK_LOCAL_ENV_FILE": str(local_env),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/configure_varlock_env.sh"),
            "--non-interactive",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    rendered = local_env.read_text(encoding="utf-8")
    assert "HYPERMEMORY_EMBEDDING_MODEL=ollama/nomic-embed-text" in rendered
    assert "HYPERMEMORY_EMBEDDING_API_BASE=http://host.docker.internal:11434" in rendered
    assert (
        "Configured HYPERMEMORY_EMBEDDING_MODEL=ollama/nomic-embed-text from local Ollama."
        in result.stdout
    )
    assert (
        "Configured HYPERMEMORY_EMBEDDING_API_BASE=http://host.docker.internal:11434 for the LiteLLM sidecar."
        in result.stdout
    )
    assert log_path.read_text(encoding="utf-8").splitlines() == [f"load --path {env_dir}"]


def test_launch_gateway_with_varlock_uses_installed_varlock_when_not_on_path(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "varlock.log"
    home_dir = tmp_path / "home"
    install_dir = home_dir / ".config" / "varlock" / "bin"
    install_dir.mkdir(parents=True)
    _write_fake_varlock(install_dir, log_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_openclaw(bin_dir)
    xdg_config_home = home_dir / ".config"
    env = os.environ | {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home_dir),
        "XDG_CONFIG_HOME": str(xdg_config_home),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/ops/launch_gateway_with_varlock.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        f"run --path {repo_root / 'platform/configs/varlock'} -- openclaw gateway"
    ]


def test_launch_sidecars_with_varlock_uses_installed_varlock_when_not_on_path(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "varlock.log"
    home_dir = tmp_path / "home"
    install_dir = home_dir / ".config" / "varlock" / "bin"
    install_dir.mkdir(parents=True)
    _write_fake_varlock(install_dir, log_path)
    xdg_config_home = home_dir / ".config"
    env = os.environ | {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home_dir),
        "XDG_CONFIG_HOME": str(xdg_config_home),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/ops/launch_sidecars_with_varlock.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        (
            f"run --path {repo_root / 'platform/configs/varlock'} -- docker compose -f "
            "docker-compose.aux-stack.yaml up -d"
        )
    ]


def test_launch_sidecars_dev_pins_repo_local_compose_state(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    docker_log = tmp_path / "docker.log"
    env_log = tmp_path / "docker.env"
    bin_dir = _build_tool_path(tmp_path, [])
    _write_fake_docker(bin_dir, log_path=docker_log, env_log_path=env_log)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/ops/launch_sidecars_dev.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        "compose -f docker-compose.aux-stack.yaml up -d"
    ]
    assert env_log.read_text(encoding="utf-8").splitlines() == [
        f"STRONGCLAW_COMPOSE_STATE_DIR={repo_root / 'platform/compose/state'}"
    ]


def test_stop_sidecars_dev_pins_repo_local_compose_state(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    docker_log = tmp_path / "docker.log"
    env_log = tmp_path / "docker.env"
    bin_dir = _build_tool_path(tmp_path, [])
    _write_fake_docker(bin_dir, log_path=docker_log, env_log_path=env_log)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/ops/stop_sidecars_dev.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert docker_log.read_text(encoding="utf-8").splitlines() == [
        "compose -f docker-compose.aux-stack.yaml down"
    ]
    assert env_log.read_text(encoding="utf-8").splitlines() == [
        f"STRONGCLAW_COMPOSE_STATE_DIR={repo_root / 'platform/compose/state'}"
    ]


def test_reset_dev_compose_state_targets_only_selected_component(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "compose-state"
    qdrant_dir = state_dir / "qdrant"
    postgres_dir = state_dir / "postgres"
    qdrant_dir.mkdir(parents=True)
    postgres_dir.mkdir(parents=True)
    (qdrant_dir / "segments.bin").write_text("old", encoding="utf-8")
    (postgres_dir / "pgdata").write_text("keep", encoding="utf-8")
    bin_dir = _build_tool_path(tmp_path, [])
    _write_fake_docker(bin_dir)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/ops/reset_dev_compose_state.sh"),
            "--component",
            "qdrant",
            "--state-dir",
            str(state_dir),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not (qdrant_dir / "segments.bin").exists()
    assert (postgres_dir / "pgdata").read_text(encoding="utf-8") == "keep"
    assert f"Reset repo-local qdrant state at {qdrant_dir}" in result.stdout


def test_reset_dev_compose_state_refuses_to_remove_running_component(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "compose-state"
    qdrant_dir = state_dir / "qdrant"
    qdrant_dir.mkdir(parents=True)
    (qdrant_dir / "segments.bin").write_text("old", encoding="utf-8")
    bin_dir = _build_tool_path(tmp_path, [])
    _write_recording_script(
        bin_dir / "docker",
        (
            'if [[ "$*" == "compose -f docker-compose.aux-stack.yaml ps -q qdrant" ]]; then\n'
            "  printf 'container-123\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/ops/reset_dev_compose_state.sh"),
            "--component",
            "qdrant",
            "--state-dir",
            str(state_dir),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert (qdrant_dir / "segments.bin").read_text(encoding="utf-8") == "old"
    assert "ERROR: qdrant is still running." in result.stderr


def test_prune_qdrant_test_collections_removes_only_legacy_memory_v2_prefix(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    delete_log = tmp_path / "delete.log"
    bin_dir = _build_tool_path(tmp_path, ["python3"])
    _write_fake_qdrant_curl(
        bin_dir / "curl",
        payload={
            "result": {
                "collections": [
                    {"name": "memory-v2-int-36a6edf9c0b1"},
                    {"name": "strongclaw-hypermemory"},
                    {"name": "hypermemory-int-abcdef"},
                    {"name": "memory-v2-int-44d1f348ccef"},
                ]
            }
        },
        delete_log=delete_log,
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/ops/prune_qdrant_test_collections.sh"),
            "--qdrant-url",
            "http://qdrant.test",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert delete_log.read_text(encoding="utf-8").splitlines() == [
        "http://qdrant.test/collections/memory-v2-int-36a6edf9c0b1",
        "http://qdrant.test/collections/memory-v2-int-44d1f348ccef",
    ]
    assert "Pruned memory-v2-int-36a6edf9c0b1" in result.stdout
    assert "Pruned memory-v2-int-44d1f348ccef" in result.stdout
    assert "strongclaw-hypermemory" not in delete_log.read_text(encoding="utf-8")


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
        f"  /bin/cat > \"{bin_dir / 'docker'}\" <<'EOF'\n"
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


def test_setup_script_composes_bootstrap_service_activation_and_verification(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "install.log"
    bootstrap_script = tmp_path / "bootstrap.sh"
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    install_script = tmp_path / "install_host_services.sh"
    verify_script = tmp_path / "verify_baseline.sh"
    configure_models_script = tmp_path / "configure_openclaw_model_auth.sh"
    _write_recording_script(
        bootstrap_script,
        f'printf "bootstrap profile=%s\\n" "${{OPENCLAW_CONFIG_PROFILE:-hypermemory}}" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_varlock_env_script,
        f'printf "validate\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_models_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{log_path}"\n',
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
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_models_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
        "VERIFY_BASELINE_SCRIPT": str(verify_script),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/setup.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "bootstrap profile=hypermemory",
        "validate",
        "configure-model-auth --probe",
        "install --activate",
        "verify",
    ]


def test_setup_script_skip_bootstrap_supports_post_bootstrap_followups(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "install-profile.log"
    bootstrap_script = tmp_path / "bootstrap.sh"
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    doctor_script = tmp_path / "doctor_host.sh"
    configure_models_script = tmp_path / "configure_openclaw_model_auth.sh"
    install_script = tmp_path / "install_host_services.sh"
    bootstrap_qmd_script = tmp_path / "bootstrap_qmd.sh"
    bootstrap_memory_plugin_script = tmp_path / "bootstrap_memory_plugin.sh"
    _write_recording_script(
        bootstrap_script,
        f'printf "bootstrap profile=%s\\n" "${{OPENCLAW_CONFIG_PROFILE:-hypermemory}}" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_qmd_script,
        f'printf "bootstrap-qmd\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_memory_plugin_script,
        f'printf "bootstrap-memory-plugin\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_varlock_env_script,
        f'printf "validate %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        render_script,
        f'printf "render %s profile=%s\\n" "$*" "${{OPENCLAW_CONFIG_PROFILE:-hypermemory}}" >> "{log_path}"\n',
    )
    _write_recording_script(
        doctor_script,
        f'printf "doctor\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_models_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        install_script,
        f'printf "install %s\\n" "$*" >> "{log_path}"\n',
    )
    env = os.environ | {
        "BOOTSTRAP_SCRIPT": str(bootstrap_script),
        "BOOTSTRAP_QMD_SCRIPT": str(bootstrap_qmd_script),
        "BOOTSTRAP_MEMORY_PLUGIN_SCRIPT": str(bootstrap_memory_plugin_script),
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "RENDER_OPENCLAW_CONFIG_SCRIPT": str(render_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_models_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/setup.sh"),
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
        "bootstrap-qmd",
        "validate ",
        "render --profile acp profile=acp",
        "doctor",
        "configure-model-auth --probe",
        "install ",
    ]


def test_setup_script_auto_skips_bootstrap_when_state_marker_exists(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "setup.log"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bootstrap.env").write_text(
        "PROFILE=openclaw-default\nHOST_OS=Linux\nRUNTIME_USER=tester\nCOMPLETED_AT=2026-03-19T00:00:00Z\n",
        encoding="utf-8",
    )
    bootstrap_script = tmp_path / "bootstrap.sh"
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    doctor_script = tmp_path / "doctor_host.sh"
    configure_models_script = tmp_path / "configure_openclaw_model_auth.sh"
    install_script = tmp_path / "install_host_services.sh"
    bootstrap_qmd_script = tmp_path / "bootstrap_qmd.sh"
    bootstrap_memory_plugin_script = tmp_path / "bootstrap_memory_plugin.sh"
    _write_recording_script(
        bootstrap_script,
        f'printf "bootstrap\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_qmd_script,
        f'printf "bootstrap-qmd\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_memory_plugin_script,
        f'printf "bootstrap-memory-plugin\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_varlock_env_script,
        f'printf "validate\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        render_script,
        f'printf "render %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        doctor_script,
        f'printf "doctor\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_models_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        install_script,
        f'printf "install %s\\n" "$*" >> "{log_path}"\n',
    )
    env = os.environ | {
        "OPENCLAW_SETUP_STATE_DIR": str(state_dir),
        "OPENCLAW_BOOTSTRAP_STATE_FILE": str(state_dir / "bootstrap.env"),
        "BOOTSTRAP_SCRIPT": str(bootstrap_script),
        "BOOTSTRAP_QMD_SCRIPT": str(bootstrap_qmd_script),
        "BOOTSTRAP_MEMORY_PLUGIN_SCRIPT": str(bootstrap_memory_plugin_script),
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "RENDER_OPENCLAW_CONFIG_SCRIPT": str(render_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_models_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/setup.sh"),
            "--profile",
            "acp",
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
    assert "auto-skipped (host bootstrap already completed)" in result.stdout
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "bootstrap-qmd",
        "validate",
        "render --profile acp",
        "doctor",
        "configure-model-auth --probe",
        "install ",
    ]


def test_setup_script_auto_skip_reconciles_lossless_assets_when_switching_to_hypermemory(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "setup.log"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bootstrap.env").write_text(
        "PROFILE=openclaw-default\nHOST_OS=Linux\nRUNTIME_USER=tester\nCOMPLETED_AT=2026-03-19T00:00:00Z\n",
        encoding="utf-8",
    )
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    doctor_script = tmp_path / "doctor_host.sh"
    configure_models_script = tmp_path / "configure_openclaw_model_auth.sh"
    install_script = tmp_path / "install_host_services.sh"
    bootstrap_qmd_script = tmp_path / "bootstrap_qmd.sh"
    bootstrap_memory_plugin_script = tmp_path / "bootstrap_memory_plugin.sh"
    bootstrap_lossless_context_engine_script = tmp_path / "bootstrap_lossless_context_engine.sh"
    _write_recording_script(
        bootstrap_qmd_script,
        f'printf "bootstrap-qmd\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_memory_plugin_script,
        f'printf "bootstrap-memory-plugin\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_lossless_context_engine_script,
        f'printf "bootstrap-lossless-context\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_varlock_env_script,
        f'printf "validate\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        render_script,
        f'printf "render %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        doctor_script,
        f'printf "doctor\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_models_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        install_script,
        f'printf "install %s\\n" "$*" >> "{log_path}"\n',
    )
    env = os.environ | {
        "OPENCLAW_SETUP_STATE_DIR": str(state_dir),
        "OPENCLAW_BOOTSTRAP_STATE_FILE": str(state_dir / "bootstrap.env"),
        "BOOTSTRAP_QMD_SCRIPT": str(bootstrap_qmd_script),
        "BOOTSTRAP_MEMORY_PLUGIN_SCRIPT": str(bootstrap_memory_plugin_script),
        "BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT": str(bootstrap_lossless_context_engine_script),
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "RENDER_OPENCLAW_CONFIG_SCRIPT": str(render_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_models_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/setup.sh"),
            "--profile",
            "hypermemory",
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
    assert "auto-skipped (host bootstrap already completed)" in result.stdout
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "bootstrap-lossless-context",
        "validate",
        "render --profile hypermemory",
        "doctor",
        "configure-model-auth --probe",
        "install ",
    ]


def test_setup_script_auto_skip_reconciles_qmd_assets_when_switching_to_openclaw_qmd(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "setup.log"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "bootstrap.env").write_text(
        "PROFILE=hypermemory\nHOST_OS=Linux\nRUNTIME_USER=tester\nCOMPLETED_AT=2026-03-19T00:00:00Z\n",
        encoding="utf-8",
    )
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    doctor_script = tmp_path / "doctor_host.sh"
    configure_models_script = tmp_path / "configure_openclaw_model_auth.sh"
    install_script = tmp_path / "install_host_services.sh"
    bootstrap_qmd_script = tmp_path / "bootstrap_qmd.sh"
    bootstrap_memory_plugin_script = tmp_path / "bootstrap_memory_plugin.sh"
    bootstrap_lossless_context_engine_script = tmp_path / "bootstrap_lossless_context_engine.sh"
    _write_recording_script(
        bootstrap_qmd_script,
        f'printf "bootstrap-qmd\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_memory_plugin_script,
        f'printf "bootstrap-memory-plugin\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        bootstrap_lossless_context_engine_script,
        f'printf "bootstrap-lossless-context\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_varlock_env_script,
        f'printf "validate\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        render_script,
        f'printf "render %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        doctor_script,
        f'printf "doctor\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_models_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        install_script,
        f'printf "install %s\\n" "$*" >> "{log_path}"\n',
    )
    env = os.environ | {
        "OPENCLAW_SETUP_STATE_DIR": str(state_dir),
        "OPENCLAW_BOOTSTRAP_STATE_FILE": str(state_dir / "bootstrap.env"),
        "BOOTSTRAP_QMD_SCRIPT": str(bootstrap_qmd_script),
        "BOOTSTRAP_MEMORY_PLUGIN_SCRIPT": str(bootstrap_memory_plugin_script),
        "BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT": str(bootstrap_lossless_context_engine_script),
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "RENDER_OPENCLAW_CONFIG_SCRIPT": str(render_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_models_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/setup.sh"),
            "--profile",
            "openclaw-qmd",
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
    assert "auto-skipped (host bootstrap already completed)" in result.stdout
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "bootstrap-qmd",
        "validate",
        "render --profile openclaw-qmd",
        "doctor",
        "configure-model-auth --probe",
        "install ",
    ]


def test_setup_script_skips_baseline_when_services_are_not_activated(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    log_path = tmp_path / "setup.log"
    bootstrap_script = tmp_path / "bootstrap.sh"
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    configure_models_script = tmp_path / "configure_openclaw_model_auth.sh"
    install_script = tmp_path / "install_host_services.sh"
    verify_script = tmp_path / "verify_baseline.sh"
    _write_recording_script(
        bootstrap_script,
        f'printf "bootstrap\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_varlock_env_script,
        f'printf "validate\\n" >> "{log_path}"\n',
    )
    _write_recording_script(
        configure_models_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{log_path}"\n',
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
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_models_script),
        "INSTALL_HOST_SERVICES_SCRIPT": str(install_script),
        "VERIFY_BASELINE_SCRIPT": str(verify_script),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/setup.sh"),
            "--no-activate-services",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (
        "Baseline verification requires active gateway and sidecar services; skipping it because --no-activate-services was selected."
        in result.stdout
    )
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "bootstrap",
        "validate",
        "configure-model-auth --probe",
        "install ",
    ]


def test_doctor_strongclaw_runs_deep_checks_with_model_probe_by_default(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    helper_log = tmp_path / "helpers.log"
    openclaw_log = tmp_path / "openclaw.log"
    clawops_log = tmp_path / "clawops.log"
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    doctor_host_script = tmp_path / "doctor_host.sh"
    configure_model_auth_script = tmp_path / "configure_openclaw_model_auth.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_recording_script(
        configure_varlock_env_script,
        f'printf "configure-varlock %s\\n" "$*" >> "{helper_log}"\n',
    )
    _write_recording_script(
        doctor_host_script,
        f'printf "doctor-host %s\\n" "$*" >> "{helper_log}"\n',
    )
    _write_recording_script(
        configure_model_auth_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{helper_log}"\n',
    )
    _write_recording_script(
        bin_dir / "openclaw",
        f'printf "openclaw %s\\n" "$*" >> "{openclaw_log}"\n',
    )
    fake_clawops = tmp_path / "clawops"
    _write_recording_script(
        fake_clawops,
        f'printf "clawops %s\\n" "$*" >> "{clawops_log}"\n',
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_host_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_model_auth_script),
        "CLAWOPS_BIN": str(fake_clawops),
        "OPENCLAW_VARLOCK_ENV_PATH": str(tmp_path / "missing-varlock"),
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/doctor_strongclaw.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert helper_log.read_text(encoding="utf-8").splitlines() == [
        "configure-varlock --check-only",
        "doctor-host ",
        "configure-model-auth --check-only --probe",
    ]
    assert openclaw_log.read_text(encoding="utf-8").splitlines() == [
        "openclaw doctor --non-interactive",
        "openclaw security audit --deep",
        "openclaw secrets audit --check",
        "openclaw gateway status --json",
        "openclaw memory status --deep",
    ]
    assert clawops_log.read_text(encoding="utf-8").splitlines() == [
        "clawops verify-platform sidecars",
        "clawops verify-platform observability",
        "clawops verify-platform channels",
    ]


def test_doctor_strongclaw_skip_runtime_skips_runtime_only_checks(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    helper_log = tmp_path / "helpers.log"
    openclaw_log = tmp_path / "openclaw.log"
    clawops_log = tmp_path / "clawops.log"
    configure_varlock_env_script = tmp_path / "configure_varlock_env.sh"
    doctor_host_script = tmp_path / "doctor_host.sh"
    configure_model_auth_script = tmp_path / "configure_openclaw_model_auth.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_recording_script(
        configure_varlock_env_script,
        f'printf "configure-varlock %s\\n" "$*" >> "{helper_log}"\n',
    )
    _write_recording_script(
        doctor_host_script,
        f'printf "doctor-host %s\\n" "$*" >> "{helper_log}"\n',
    )
    _write_recording_script(
        configure_model_auth_script,
        f'printf "configure-model-auth %s\\n" "$*" >> "{helper_log}"\n',
    )
    _write_recording_script(
        bin_dir / "openclaw",
        f'printf "openclaw %s\\n" "$*" >> "{openclaw_log}"\n',
    )
    fake_clawops = tmp_path / "clawops"
    _write_recording_script(
        fake_clawops,
        f'printf "clawops %s\\n" "$*" >> "{clawops_log}"\n',
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CONFIGURE_VARLOCK_ENV_SCRIPT": str(configure_varlock_env_script),
        "DOCTOR_HOST_SCRIPT": str(doctor_host_script),
        "CONFIGURE_MODEL_AUTH_SCRIPT": str(configure_model_auth_script),
        "CLAWOPS_BIN": str(fake_clawops),
        "OPENCLAW_VARLOCK_ENV_PATH": str(tmp_path / "missing-varlock"),
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(repo_root / "scripts/bootstrap/doctor_strongclaw.sh"),
            "--skip-runtime",
            "--no-model-probe",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert helper_log.read_text(encoding="utf-8").splitlines() == [
        "configure-varlock --check-only",
        "doctor-host ",
        "configure-model-auth --check-only",
    ]
    assert openclaw_log.read_text(encoding="utf-8").splitlines() == [
        "openclaw doctor --non-interactive",
        "openclaw security audit --deep",
        "openclaw secrets audit --check",
    ]
    assert clawops_log.read_text(encoding="utf-8").splitlines() == [
        "clawops verify-platform sidecars --skip-runtime",
        "clawops verify-platform observability --skip-runtime",
        "clawops verify-platform channels",
    ]


def test_darwin_bootstrap_reuses_existing_toolchain_without_brew_reinstalling_node(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    harness_root = tmp_path / "repo"
    shutil.copytree(repo_root / "scripts", harness_root / "scripts")
    log_path = tmp_path / "bootstrap.log"
    brew_log = tmp_path / "brew.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_recording_script(
        harness_root / "scripts/lib/docker_runtime.sh",
        "ensure_docker_compatible_runtime() { :; }\n"
        "repair_linux_runtime_user_docker_access() { :; }\n"
        "DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP=0\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/preflight.sh",
        "printf 'preflight\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/bootstrap_qmd.sh",
        "printf 'bootstrap_qmd\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/bootstrap_memory_plugin.sh",
        "printf 'bootstrap_memory_plugin\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/render_openclaw_config.sh",
        "printf 'render_openclaw_config\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/bootstrap_lossless_context_engine.sh",
        "printf 'bootstrap_lossless_context_engine\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/doctor_host.sh",
        "printf 'doctor_host\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )

    _write_recording_script(
        bin_dir / "uname",
        'if [[ "${1:-}" == "-m" ]]; then printf "x86_64\\n"; exit 0; fi\nprintf \'Darwin\\n\'\n',
    )
    _write_recording_script(bin_dir / "brew", f'printf "%s\\n" "$*" >> "{brew_log}"\n')
    _write_recording_script(
        bin_dir / "python3",
        'if [[ "${1:-}" == "-c" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then exit 0; fi\n'
        'printf "unexpected python3 args: %s\\n" "$*" >&2\n'
        "exit 1\n",
    )
    _write_recording_script(
        bin_dir / "node",
        'if [[ "${1:-}" == "-e" ]]; then exit 0; fi\nprintf \'v24.13.1\\n\'\n',
    )
    _write_recording_script(
        bin_dir / "npm",
        f'printf "npm %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(bin_dir / "jq", "exit 0\n")
    _write_recording_script(bin_dir / "sqlite3", "exit 0\n")
    _write_recording_script(bin_dir / "bun", "exit 0\n")
    _write_recording_script(
        bin_dir / "uv",
        f'printf "uv %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(bin_dir / "openclaw", "exit 0\n")
    _write_recording_script(bin_dir / "acpx", "exit 0\n")
    home_dir = tmp_path / "home"
    install_dir = home_dir / ".config" / "varlock" / "bin"
    install_dir.mkdir(parents=True)
    _write_recording_script(
        install_dir / "varlock",
        'if [[ "${1:-}" == "--version" ]]; then printf "varlock 0.5.0\\n"; exit 0; fi\nexit 0\n',
    )
    xdg_config_home = home_dir / ".config"

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "BOOTSTRAP_LOG_PATH": str(log_path),
    }

    result = subprocess.run(
        ["/bin/bash", str(harness_root / "scripts/bootstrap/bootstrap.sh")],
        cwd=harness_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "preflight",
        f"uv sync --project {harness_root} --python 3.12 --locked --extra dev",
        "npm install -g openclaw@2026.3.13 acpx@0.3.0",
        "bootstrap_lossless_context_engine",
        "render_openclaw_config",
        "doctor_host",
    ]
    assert not brew_log.exists()


def test_darwin_bootstrap_installs_lossless_claw_for_hypermemory_profile(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    harness_root = tmp_path / "repo"
    shutil.copytree(repo_root / "scripts", harness_root / "scripts")
    log_path = tmp_path / "bootstrap.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _write_recording_script(
        harness_root / "scripts/lib/docker_runtime.sh",
        "ensure_docker_compatible_runtime() { :; }\n"
        "repair_linux_runtime_user_docker_access() { :; }\n"
        "DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP=0\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/preflight.sh",
        "printf 'preflight\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/bootstrap_qmd.sh",
        "printf 'bootstrap_qmd\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/bootstrap_memory_plugin.sh",
        "printf 'bootstrap_memory_plugin\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/bootstrap_lossless_context_engine.sh",
        "printf 'bootstrap_lossless_context_engine\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/render_openclaw_config.sh",
        "printf 'render_openclaw_config\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )
    _write_recording_script(
        harness_root / "scripts/bootstrap/doctor_host.sh",
        "printf 'doctor_host\\n' >> \"$BOOTSTRAP_LOG_PATH\"\n",
    )

    _write_recording_script(
        bin_dir / "uname",
        'if [[ "${1:-}" == "-m" ]]; then printf "x86_64\\n"; exit 0; fi\nprintf \'Darwin\\n\'\n',
    )
    _write_recording_script(bin_dir / "python3", 'if [[ "${1:-}" == "-c" ]]; then exit 0; fi\n')
    _write_recording_script(
        bin_dir / "node", 'if [[ "${1:-}" == "-e" ]]; then exit 0; fi\nprintf "v24.13.1\\n"\n'
    )
    _write_recording_script(bin_dir / "npm", f'printf "npm %s\\n" "$*" >> "{log_path}"\n')
    _write_recording_script(bin_dir / "jq", "exit 0\n")
    _write_recording_script(bin_dir / "sqlite3", "exit 0\n")
    _write_recording_script(bin_dir / "bun", "exit 0\n")
    _write_recording_script(
        bin_dir / "uv",
        f'printf "uv %s\\n" "$*" >> "{log_path}"\n',
    )
    _write_recording_script(
        bin_dir / "varlock",
        'if [[ "${1:-}" == "--version" ]]; then printf "varlock 0.5.0\\n"; exit 0; fi\nexit 0\n',
    )
    _write_recording_script(bin_dir / "openclaw", "exit 0\n")
    _write_recording_script(bin_dir / "acpx", "exit 0\n")
    _write_recording_script(bin_dir / "brew", "exit 1\n")

    home_dir = tmp_path / "home"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "BOOTSTRAP_LOG_PATH": str(log_path),
        "OPENCLAW_CONFIG_PROFILE": "hypermemory",
    }

    result = subprocess.run(
        ["/bin/bash", str(harness_root / "scripts/bootstrap/bootstrap.sh")],
        cwd=harness_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "preflight",
        f"uv sync --project {harness_root} --python 3.12 --locked --extra dev",
        "npm install -g openclaw@2026.3.13 acpx@0.3.0",
        "bootstrap_lossless_context_engine",
        "render_openclaw_config",
        "doctor_host",
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
    assert "== Varlock version ==" in result.stdout
    assert "varlock 0.5.0" in result.stdout
    assert "config ok" in result.stdout
    assert "validated " in result.stdout


def test_doctor_host_uses_hypermemory_status_json_to_gate_hypermemory_verification(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "jq"])
    _write_fake_openclaw(bin_dir)
    _write_fake_acpx(bin_dir)
    _write_fake_varlock(bin_dir)
    clawops_log = tmp_path / "clawops.log"
    _write_fake_clawops_with_hypermemory_status(
        bin_dir,
        status_payload={"backendActive": "qdrant_sparse_dense_hybrid"},
        log_path=clawops_log,
    )
    home_dir = tmp_path / "home"
    hypermemory_config = tmp_path / "hypermemory.sqlite.yaml"
    hypermemory_config.write_text(
        "backend:\n  active: qdrant_sparse_dense_hybrid\n", encoding="utf-8"
    )
    lossless_dir = tmp_path / "plugins" / "lossless-claw"
    lossless_dir.mkdir(parents=True)
    lossless_dir.joinpath("openclaw.plugin.json").write_text("{}", encoding="utf-8")
    config_path = home_dir / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "gateway": {"bind": "loopback"},
                "plugins": {
                    "load": {"paths": [lossless_dir.as_posix()]},
                    "slots": {
                        "contextEngine": "lossless-claw",
                        "memory": "strongclaw-hypermemory",
                    },
                    "entries": {
                        "strongclaw-hypermemory": {
                            "config": {"configPath": hypermemory_config.as_posix()}
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "CLAWOPS_PREFER_PATH": "1",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/doctor_host.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert clawops_log.read_text(encoding="utf-8").splitlines() == [
        f"hypermemory status --config {hypermemory_config} --json",
        f"hypermemory verify --config {hypermemory_config} --json",
    ]


def test_doctor_host_skips_hypermemory_verification_for_dense_only_hypermemory_backend(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "jq"])
    _write_fake_openclaw(bin_dir)
    _write_fake_acpx(bin_dir)
    _write_fake_varlock(bin_dir)
    clawops_log = tmp_path / "clawops.log"
    _write_fake_clawops_with_hypermemory_status(
        bin_dir,
        status_payload={"backendActive": "qdrant_dense_hybrid"},
        log_path=clawops_log,
    )
    home_dir = tmp_path / "home"
    hypermemory_config = tmp_path / "hypermemory.sqlite.yaml"
    hypermemory_config.write_text("backend:\n  active: qdrant_dense_hybrid\n", encoding="utf-8")
    config_path = home_dir / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "gateway": {"bind": "loopback"},
                "plugins": {
                    "slots": {"memory": "strongclaw-hypermemory"},
                    "entries": {
                        "strongclaw-hypermemory": {
                            "config": {"configPath": hypermemory_config.as_posix()}
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home_dir),
        "CLAWOPS_PREFER_PATH": "1",
    }

    result = subprocess.run(
        ["/bin/bash", str(repo_root / "scripts/bootstrap/doctor_host.sh")],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert clawops_log.read_text(encoding="utf-8").splitlines() == [
        f"hypermemory status --config {hypermemory_config} --json"
    ]


def test_backup_create_warns_and_falls_back_without_openclaw(tmp_path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    bin_dir = _build_tool_path(tmp_path, ["dirname", "uname", "date", "mkdir", "tar", "gzip"])
    home = tmp_path / "home"
    claw_home = home / ".openclaw"
    claw_home.mkdir(parents=True)
    (claw_home / "state.txt").write_text("ok\n", encoding="utf-8")
    env = os.environ | {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home)}

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
    env = os.environ | {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home)}

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
    bin_dir = _build_tool_path(
        tmp_path, ["bash", "dirname", "uname", "ls", "head", "mkdir", "tar", "gzip"]
    )
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
    env = os.environ | {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home)}

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
    harness_root = tmp_path / "repo"
    (harness_root / "scripts/ops").mkdir(parents=True)
    (harness_root / "scripts/lib").mkdir(parents=True)
    (harness_root / "platform/configs/workflows").mkdir(parents=True)
    shutil.copy2(
        repo_root / "scripts/ops/run_workflow.sh", harness_root / "scripts/ops/run_workflow.sh"
    )
    shutil.copy2(repo_root / "scripts/lib/clawops.sh", harness_root / "scripts/lib/clawops.sh")
    shutil.copy2(
        repo_root / "platform/configs/workflows/daily_healthcheck.yaml",
        harness_root / "platform/configs/workflows/daily_healthcheck.yaml",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_clawops(bin_dir)
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            "/bin/bash",
            str(harness_root / "scripts/ops/run_workflow.sh"),
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
        f"{harness_root / 'platform/configs/workflows/daily_healthcheck.yaml'} "
        f"--base-dir {harness_root} --dry-run"
    ) in result.stdout
