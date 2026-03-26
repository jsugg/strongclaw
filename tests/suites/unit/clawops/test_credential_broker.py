"""Tests for backend credential readiness checks."""

from __future__ import annotations

import os
import pathlib

from clawops.credential_broker import CredentialBroker
from tests.utils.helpers.cli import PathPrepender, write_status_script


def test_subscription_readiness_is_machine_readable_and_sanitizes_env(
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
) -> None:
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)

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
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
) -> None:
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)

    status = CredentialBroker().evaluate(
        "codex",
        required_auth_mode="api",
        environ={"PATH": os.environ["PATH"]},
    )

    assert status.ready is False
    assert status.state == "forbidden_by_policy"
    assert "required auth mode api is not ready" in status.message


def test_claude_subscription_status_supports_json_output(
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
) -> None:
    write_status_script(
        cli_bin_dir,
        "claude",
        stdout_text='{"status":"authenticated","authMethod":"claudeai"}',
    )
    prepend_path(cli_bin_dir)

    status = CredentialBroker().evaluate(
        "claude",
        required_auth_mode="subscription",
        environ={"PATH": os.environ["PATH"]},
    )

    assert status.ready is True
    assert status.state == "ready"
    assert status.metadata["status"] == "authenticated"
