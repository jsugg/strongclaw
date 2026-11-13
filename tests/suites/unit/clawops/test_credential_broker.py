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


def test_api_mode_grants_present_provider_keys_to_acpx_auth() -> None:
    """ACPX 0.6+ selects ACP auth only from `ACPX_AUTH_*`, so API mode re-exports the key."""
    environ = {"PATH": os.environ["PATH"], "OPENAI_API_KEY": "secret"}

    status = CredentialBroker().evaluate("codex", required_auth_mode="api", environ=environ)
    dispatch_env = status.sanitized_env(environ)

    assert status.ready is True
    assert status.auth_grant_env_keys == ("OPENAI_API_KEY",)
    assert dispatch_env["OPENAI_API_KEY"] == "secret"
    assert dispatch_env["ACPX_AUTH_OPENAI_API_KEY"] == "secret"
    assert status.to_dict()["auth_grant_env_keys"] == ["OPENAI_API_KEY"]


def test_api_mode_keeps_an_explicit_acpx_auth_grant() -> None:
    """An operator-provided grant wins over the mirrored provider key."""
    environ = {
        "PATH": os.environ["PATH"],
        "ANTHROPIC_API_KEY": "provider-secret",
        "ACPX_AUTH_ANTHROPIC_API_KEY": "explicit-secret",
    }

    status = CredentialBroker().evaluate("claude", required_auth_mode="api", environ=environ)

    assert status.sanitized_env(environ)["ACPX_AUTH_ANTHROPIC_API_KEY"] == "explicit-secret"


def test_subscription_mode_strips_the_matching_acpx_auth_grant(
    cli_bin_dir: pathlib.Path,
    prepend_path: PathPrepender,
) -> None:
    """Subscription dispatch must not fall back to API-key auth through an ACPX grant."""
    write_status_script(cli_bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    prepend_path(cli_bin_dir)
    environ = {
        "PATH": os.environ["PATH"],
        "OPENAI_API_KEY": "secret",
        "ACPX_AUTH_OPENAI_API_KEY": "secret",
    }

    status = CredentialBroker().evaluate(
        "codex", required_auth_mode="subscription", environ=environ
    )
    dispatch_env = status.sanitized_env(environ)

    assert status.ready is True
    assert status.auth_grant_env_keys == ()
    assert "OPENAI_API_KEY" not in dispatch_env
    assert "ACPX_AUTH_OPENAI_API_KEY" not in dispatch_env
