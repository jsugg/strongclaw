"""Unit tests for the root CLI contract."""

from __future__ import annotations

from clawops.cli import main


def test_root_help_is_available(capsys: object) -> None:
    exit_code = main(["--help"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "available commands:" in captured.out
    assert "bootstrap" in captured.out
    assert "doctor-host" in captured.out
    assert "model-auth" in captured.out
    assert "services" in captured.out
    assert "ops" in captured.out
    assert "baseline" in captured.out
    assert "recovery" in captured.out
    assert "varlock-env" in captured.out


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
