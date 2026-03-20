"""Tests for the guided setup CLI entrypoints."""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from clawops import setup_cli


def test_setup_cli_runs_shell_entrypoint_via_bash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    script_path = tmp_path / "setup.sh"
    script_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    captured: list[list[str]] = []

    def _run(command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        captured.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("CLAWOPS_SETUP_SCRIPT", str(script_path))
    monkeypatch.setattr(setup_cli.subprocess, "run", _run)

    result = setup_cli.setup_main(["--skip-bootstrap"])

    assert result == 0
    assert captured == [["/bin/bash", str(script_path), "--skip-bootstrap"]]
