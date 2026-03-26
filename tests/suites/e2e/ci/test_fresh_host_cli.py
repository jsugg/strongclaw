"""End-to-end coverage for fresh-host CLI orchestration."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from tests.fixtures.repo import REPO_ROOT


def _write_executable(path: Path, body: str) -> None:
    """Create one executable script."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _prepare_fake_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a synthetic repo root with fake Docker and venv entrypoints."""
    repo_root = tmp_path / "repo"
    compose_dir = repo_root / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    for compose_name in ("docker-compose.aux-stack.yaml", "docker-compose.browser-lab.yaml"):
        (compose_dir / compose_name).write_text("services: {}\n", encoding="utf-8")

    log_path = tmp_path / "command.log"
    fake_bin = tmp_path / "fake-bin"
    _write_executable(
        repo_root / ".venv" / "bin" / "python",
        "\n".join(
            [
                f"#!{sys.executable}",
                "from __future__ import annotations",
                "import os",
                "import sys",
                "",
                "with open(os.environ['FRESH_HOST_E2E_LOG'], 'a', encoding='utf-8') as handle:",
                "    handle.write('python:' + ' '.join(sys.argv[1:]) + '\\n')",
            ]
        ),
    )
    _write_executable(
        fake_bin / "docker",
        "\n".join(
            [
                f"#!{sys.executable}",
                "from __future__ import annotations",
                "import json",
                "import os",
                "import sys",
                "",
                "state_path = os.environ.get('FRESH_HOST_E2E_STATE', '')",
                "args = sys.argv[1:]",
                "with open(os.environ['FRESH_HOST_E2E_LOG'], 'a', encoding='utf-8') as handle:",
                "    handle.write('docker:' + ' '.join(args) + '\\n')",
                "if args == ['info']:",
                "    raise SystemExit(0)",
                "if len(args) >= 6 and args[0] == 'compose' and args[1] == '-f' and args[3:] == ['ps', '--format', 'json']:",
                "    compose_file = args[2]",
                "    mode = os.environ.get('FRESH_HOST_E2E_DOCKER_MODE', 'ok')",
                "    attempts = {}",
                "    if state_path and os.path.exists(state_path):",
                "        with open(state_path, 'r', encoding='utf-8') as handle:",
                "            attempts = json.load(handle)",
                "    attempt_key = compose_file + ':ps'",
                "    attempt = int(attempts.get(attempt_key, 0))",
                "    attempts[attempt_key] = attempt + 1",
                "    if state_path:",
                "        with open(state_path, 'w', encoding='utf-8') as handle:",
                "            json.dump(attempts, handle)",
                "    if compose_file.endswith('docker-compose.aux-stack.yaml'):",
                "        payload = [",
                "            {'Service': 'postgres', 'State': 'running', 'Health': 'healthy'},",
                "            {'Service': 'litellm', 'State': 'running', 'Health': 'healthy'},",
                "            {'Service': 'otel-collector', 'State': 'running'},",
                "            {'Service': 'qdrant', 'State': 'running', 'Health': 'healthy'},",
                "        ]",
                "    elif mode == 'missing-browser-service':",
                "        payload = [{'Service': 'browserlab-proxy', 'State': 'running'}]",
                "    elif mode == 'empty-first' and attempt == 0:",
                "        payload = None",
                "    else:",
                "        payload = [",
                "            {'Service': 'browserlab-proxy', 'State': 'running'},",
                "            {'Service': 'browserlab-playwright', 'State': 'running'},",
                "        ]",
                "    if payload is not None:",
                "        sys.stdout.write(json.dumps(payload))",
                "    raise SystemExit(0)",
                "raise SystemExit(0)",
            ]
        ),
    )
    return repo_root, fake_bin, log_path


def _fresh_host_command(*arguments: str) -> list[str]:
    """Return one subprocess command for the fresh-host CLI."""
    return [sys.executable, str(REPO_ROOT / "tests" / "scripts" / "fresh_host.py"), *arguments]


def _run_fresh_host(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one fresh-host CLI subprocess."""
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _load_exports(path: Path) -> dict[str, str]:
    """Read key-value exports from one GitHub env file."""
    exports: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", maxsplit=1)
        exports[key] = value
    return exports


def _set_phase_names(context_path: Path, phase_names: list[str]) -> None:
    """Rewrite the prepared context with a narrow phase plan."""
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    payload["phase_names"] = phase_names
    context_path.write_text(json.dumps(payload), encoding="utf-8")


def _prepare_linux_context(
    tmp_path: Path,
    *,
    docker_mode: str = "ok",
) -> tuple[dict[str, str], dict[str, str], Path]:
    """Prepare one Linux fresh-host context in a synthetic repo."""
    repo_root, fake_bin, log_path = _prepare_fake_repo(tmp_path)
    github_env = tmp_path / "github.env"
    runner_temp = tmp_path / "runner-temp"
    state_path = tmp_path / "docker-state.json"
    env = dict(os.environ)
    env.update(
        {
            "GITHUB_EVENT_NAME": "push",
            "FRESH_HOST_E2E_DOCKER_MODE": docker_mode,
            "FRESH_HOST_E2E_LOG": str(log_path),
            "FRESH_HOST_E2E_STATE": str(state_path),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
        }
    )
    prepared = _run_fresh_host(
        _fresh_host_command(
            "prepare-context",
            "--scenario",
            "linux",
            "--repo-root",
            str(repo_root),
            "--workspace",
            str(repo_root),
            "--runner-temp",
            str(runner_temp),
            "--github-env-file",
            str(github_env),
        ),
        env=env,
    )
    assert prepared.returncode == 0, prepared.stderr
    return env, _load_exports(github_env), log_path


def test_fresh_host_cli_linux_sidecars_verifies_runtime_before_teardown(tmp_path: Path) -> None:
    """The e2e CLI lane should keep sidecar verification between up and down."""
    env, exports, log_path = _prepare_linux_context(tmp_path)
    context_path = Path(exports["FRESH_HOST_CONTEXT"])
    compose_file = Path(
        tmp_path / "repo" / "platform" / "compose" / "docker-compose.aux-stack.yaml"
    )
    _set_phase_names(context_path, ["exercise-sidecars"])

    completed = _run_fresh_host(
        _fresh_host_command("run-scenario", "--context", str(context_path)),
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(Path(exports["FRESH_HOST_REPORT_JSON"]).read_text(encoding="utf-8"))
    assert report["status"] == "success"
    assert [phase["name"] for phase in report["phases"]] == ["exercise-sidecars"]
    assert [phase["status"] for phase in report["phases"]] == ["success"]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "docker:info",
        "python:-m clawops ops --repo-root . sidecars up --repo-local-state",
        f"docker:compose -f {compose_file} ps --format json",
        "python:-m clawops ops --repo-root . sidecars down --repo-local-state",
    ]


def test_fresh_host_cli_linux_browser_lab_verifies_runtime_before_teardown(
    tmp_path: Path,
) -> None:
    """The e2e CLI lane should prove browser-lab runtime state before teardown."""
    env, exports, log_path = _prepare_linux_context(tmp_path)
    context_path = Path(exports["FRESH_HOST_CONTEXT"])
    summary_path = tmp_path / "summary.md"
    compose_file = Path(
        tmp_path / "repo" / "platform" / "compose" / "docker-compose.browser-lab.yaml"
    )
    _set_phase_names(context_path, ["exercise-browser-lab"])

    completed = _run_fresh_host(
        _fresh_host_command("run-scenario", "--context", str(context_path)),
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(Path(exports["FRESH_HOST_REPORT_JSON"]).read_text(encoding="utf-8"))
    assert report["status"] == "success"
    assert [phase["name"] for phase in report["phases"]] == ["exercise-browser-lab"]
    assert [phase["status"] for phase in report["phases"]] == ["success"]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "docker:info",
        "python:-m clawops ops --repo-root . browser-lab up --repo-local-state",
        f"docker:compose -f {compose_file} ps --format json",
        "python:-m clawops ops --repo-root . browser-lab down --repo-local-state",
    ]

    summary = _run_fresh_host(
        _fresh_host_command(
            "write-summary",
            "--context",
            str(context_path),
            "--summary-file",
            str(summary_path),
        ),
        env=env,
    )
    assert summary.returncode == 0, summary.stderr
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "## Linux Fresh Host" in summary_text
    assert "| Status | success |" in summary_text
    assert "exercise-browser-lab" in summary_text


def test_fresh_host_cli_linux_browser_lab_retries_after_empty_compose_ps_output(
    tmp_path: Path,
) -> None:
    """The e2e CLI lane should tolerate transient empty compose state output."""
    env, exports, log_path = _prepare_linux_context(tmp_path, docker_mode="empty-first")
    context_path = Path(exports["FRESH_HOST_CONTEXT"])
    compose_file = Path(
        tmp_path / "repo" / "platform" / "compose" / "docker-compose.browser-lab.yaml"
    )
    _set_phase_names(context_path, ["exercise-browser-lab"])

    completed = _run_fresh_host(
        _fresh_host_command("run-scenario", "--context", str(context_path)),
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "docker:info",
        "python:-m clawops ops --repo-root . browser-lab up --repo-local-state",
        f"docker:compose -f {compose_file} ps --format json",
        f"docker:compose -f {compose_file} ps --format json",
        "python:-m clawops ops --repo-root . browser-lab down --repo-local-state",
    ]


def test_fresh_host_cli_linux_browser_lab_reports_runtime_failure(tmp_path: Path) -> None:
    """The e2e CLI lane should surface browser-lab runtime verification failures."""
    env, exports, log_path = _prepare_linux_context(tmp_path, docker_mode="missing-browser-service")
    context_path = Path(exports["FRESH_HOST_CONTEXT"])
    compose_file = Path(
        tmp_path / "repo" / "platform" / "compose" / "docker-compose.browser-lab.yaml"
    )
    _set_phase_names(context_path, ["exercise-browser-lab"])

    completed = _run_fresh_host(
        _fresh_host_command("run-scenario", "--context", str(context_path)),
        env=env,
    )

    assert completed.returncode == 1
    assert "missing expected services: browserlab-playwright" in completed.stderr
    report = json.loads(Path(exports["FRESH_HOST_REPORT_JSON"]).read_text(encoding="utf-8"))
    assert report["status"] == "failure"
    assert [phase["name"] for phase in report["phases"]] == ["exercise-browser-lab"]
    assert [phase["status"] for phase in report["phases"]] == ["failure"]
    assert "missing expected services: browserlab-playwright" in report["failure_reason"]
    log_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert log_lines[:2] == [
        "docker:info",
        "python:-m clawops ops --repo-root . browser-lab up --repo-local-state",
    ]
    assert log_lines[2:] == [f"docker:compose -f {compose_file} ps --format json"] * 11
