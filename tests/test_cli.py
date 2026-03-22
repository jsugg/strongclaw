"""Unit tests for the root CLI contract."""

from __future__ import annotations

import os
import pathlib

from clawops.cli import main


def test_root_help_is_available(capsys: object) -> None:
    exit_code = main(["--help"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "available commands:" in captured.out
    assert "approvals" in captured.out
    assert "config" in captured.out
    assert "memory" in captured.out
    assert "merge-json" in captured.out
    assert "repo" in captured.out
    assert "render-openclaw-config" in captured.out
    assert "setup" in captured.out
    assert "skills" in captured.out
    assert "doctor" in captured.out
    assert "supply-chain" in captured.out
    assert "hypermemory" in captured.out
    assert "worktree" in captured.out


def test_root_without_args_prints_usage(capsys: object) -> None:
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Companion ops, policy, context, and harness tooling" in captured.out


def test_unknown_root_command_returns_error(capsys: object) -> None:
    exit_code = main(["nope"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "unknown command: nope" in captured.out


def _write_shell_entrypoint(
    path: pathlib.Path, message: str, log_path: pathlib.Path | None = None
) -> None:
    body = "#!/usr/bin/env bash\n" "set -euo pipefail\n" f'printf "{message} %s\\n" "$*"'
    if log_path is not None:
        body += f' >> "{log_path}"'
    body += "\n"
    path.write_text(
        body,
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_setup_wrapper_executes_override_script(
    tmp_path: pathlib.Path, monkeypatch: object
) -> None:
    script = tmp_path / "setup.sh"
    log_path = tmp_path / "setup.log"
    _write_shell_entrypoint(script, "setup-script", log_path)
    monkeypatch.setenv("CLAWOPS_SETUP_SCRIPT", os.fspath(script))

    exit_code = main(["setup", "--non-interactive"])

    assert exit_code == 0
    assert log_path.read_text(encoding="utf-8").strip() == "setup-script --non-interactive"


def test_doctor_wrapper_executes_override_script(
    tmp_path: pathlib.Path, monkeypatch: object
) -> None:
    script = tmp_path / "doctor.sh"
    log_path = tmp_path / "doctor.log"
    _write_shell_entrypoint(script, "doctor-script", log_path)
    monkeypatch.setenv("CLAWOPS_DOCTOR_SCRIPT", os.fspath(script))

    exit_code = main(["doctor", "--skip-runtime"])

    assert exit_code == 0
    assert log_path.read_text(encoding="utf-8").strip() == "doctor-script --skip-runtime"
