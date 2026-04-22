"""Unit tests for process_runner.py."""

from __future__ import annotations

import sys

import pytest

from clawops.process_runner import CommandResult, run_command

# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


def test_string_command_without_shell_raises() -> None:
    with pytest.raises(ValueError, match="shell=True"):
        run_command("echo hello")


def test_list_command_with_shell_raises() -> None:
    with pytest.raises(ValueError, match="string command"):
        run_command(["echo", "hello"], shell=True)


def test_zero_timeout_raises() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        run_command(["echo", "hi"], timeout_seconds=0)


def test_negative_timeout_raises() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        run_command(["echo", "hi"], timeout_seconds=-1)


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------


def test_run_command_echo_exits_zero() -> None:
    result = run_command([sys.executable, "-c", "print('hello')"])
    assert result.ok is True
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.timed_out is False
    assert result.failed_to_start is False


def test_run_command_captures_stdout() -> None:
    result = run_command([sys.executable, "-c", "print('output line')"])
    assert "output line" in result.stdout


def test_run_command_captures_stderr() -> None:
    result = run_command([sys.executable, "-c", "import sys; sys.stderr.write('err msg\\n')"])
    assert "err msg" in result.stderr


def test_run_command_nonzero_exit_sets_ok_false() -> None:
    result = run_command([sys.executable, "-c", "import sys; sys.exit(2)"])
    assert result.ok is False
    assert result.returncode == 2
    assert result.timed_out is False
    assert result.failed_to_start is False


def test_run_command_shell_mode() -> None:
    result = run_command("exit 0", shell=True)
    assert result.ok is True
    assert result.returncode == 0


def test_run_command_duration_ms_is_non_negative() -> None:
    result = run_command([sys.executable, "-c", "pass"])
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_run_command_timeout_sets_timed_out_flag() -> None:
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
    )
    assert result.timed_out is True
    assert result.ok is False
    assert result.returncode is None


# ---------------------------------------------------------------------------
# Failed to start (OSError)
# ---------------------------------------------------------------------------


def test_run_command_nonexistent_binary_sets_failed_to_start() -> None:
    result = run_command(["/nonexistent/binary/xyz"])
    assert result.failed_to_start is True
    assert result.ok is False
    assert result.returncode is None
    assert len(result.stderr) > 0


# ---------------------------------------------------------------------------
# CommandResult.ok property
# ---------------------------------------------------------------------------


def test_command_result_ok_true_for_success() -> None:
    r = CommandResult(returncode=0, stdout="", stderr="", duration_ms=0)
    assert r.ok is True


def test_command_result_ok_false_for_nonzero_exit() -> None:
    r = CommandResult(returncode=1, stdout="", stderr="", duration_ms=0)
    assert r.ok is False


def test_command_result_ok_false_for_timed_out() -> None:
    r = CommandResult(returncode=0, stdout="", stderr="", duration_ms=0, timed_out=True)
    assert r.ok is False


def test_command_result_ok_false_for_failed_to_start() -> None:
    r = CommandResult(returncode=0, stdout="", stderr="", duration_ms=0, failed_to_start=True)
    assert r.ok is False
