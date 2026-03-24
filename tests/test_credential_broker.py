"""Tests for backend credential readiness checks."""

from __future__ import annotations

import os
import pathlib

from clawops.credential_broker import CredentialBroker


def _write_status_script(
    bin_dir: pathlib.Path,
    name: str,
    *,
    stdout_text: str,
    exit_code: int = 0,
) -> None:
    target = bin_dir / name
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == *"login status"* ]] || [[ "$*" == *"auth status"* ]]; then\n'
        f"  printf '%s\\n' {stdout_text!r}\n"
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def test_subscription_readiness_is_machine_readable_and_sanitizes_env(
    tmp_path: pathlib.Path,
    monkeypatch: object,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    broker = CredentialBroker()
    status = broker.evaluate(
        "codex",
        required_auth_mode="subscription",
        environ={"PATH": os.environ["PATH"], "OPENAI_API_KEY": "secret"},
    )

    assert status.ready is True
    assert status.state == "ready"
    assert "OPENAI_API_KEY" in status.removed_env_keys
    assert "OPENAI_API_KEY" not in status.sanitized_env(
        {"PATH": os.environ["PATH"], "OPENAI_API_KEY": "secret"}
    )


def test_policy_violation_fails_closed_when_only_subscription_is_available(
    tmp_path: pathlib.Path,
    monkeypatch: object,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    status = CredentialBroker().evaluate(
        "codex",
        required_auth_mode="api",
        environ={"PATH": os.environ["PATH"]},
    )

    assert status.ready is False
    assert status.state == "forbidden_by_policy"
    assert "required auth mode api is not ready" in status.message


def test_claude_subscription_status_supports_json_output(
    tmp_path: pathlib.Path,
    monkeypatch: object,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_status_script(
        bin_dir,
        "claude",
        stdout_text='{"status":"authenticated","authMethod":"claudeai"}',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    status = CredentialBroker().evaluate(
        "claude",
        required_auth_mode="subscription",
        environ={"PATH": os.environ["PATH"]},
    )

    assert status.ready is True
    assert status.state == "ready"
    assert status.metadata["status"] == "authenticated"
