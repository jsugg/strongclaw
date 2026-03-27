"""Tests for the ACPX adapter boundary."""

from __future__ import annotations

import pathlib

import pytest

from clawops.acpx_adapter import AcpxAdapter, AcpxInvocation, parse_acpx_output
from clawops.process_runner import CommandResult


def test_acpx_adapter_run_includes_permissions_output_and_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    invocation = AcpxInvocation(
        agent_name="codex",
        prompt="Review the diff",
        cwd=tmp_path,
        timeout_seconds=90,
        permissions_mode="approve-all",
        output_format="ndjson",
        backend_profile="gpt-5",
    )
    observed: dict[str, object] = {}

    def fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path,
        env: dict[str, str] | None,
        timeout_seconds: int,
    ) -> CommandResult:
        observed["command"] = command
        observed["cwd"] = cwd
        observed["env"] = env
        observed["timeout_seconds"] = timeout_seconds
        return CommandResult(
            returncode=0,
            stdout='{"event":"start"}\n{"event":"done"}\n',
            stderr="",
            duration_ms=11,
        )

    monkeypatch.setattr("clawops.acpx_adapter.run_command", fake_run_command)

    result = AcpxAdapter().run(invocation, env={"PATH": "/tmp/bin"})

    assert observed["command"] == [
        "acpx",
        "--approve-all",
        "--format",
        "json",
        "--json-strict",
        "--model",
        "gpt-5",
        "codex",
        "exec",
        "Review the diff",
    ]
    assert observed["cwd"] == tmp_path
    assert observed["env"] == {"PATH": "/tmp/bin"}
    assert observed["timeout_seconds"] == 90
    assert result.parsed_output.format == "ndjson"
    assert len(result.parsed_output.events) == 2


@pytest.mark.parametrize(
    ("stdout_text", "expected_format", "expected_event_count"),
    [
        ('{"ok": true}', "json", 0),
        ('{"event":"start"}\n{"event":"done"}', "ndjson", 2),
    ],
)
def test_parse_acpx_output_supports_structured_formats(
    stdout_text: str,
    expected_format: str,
    expected_event_count: int,
) -> None:
    parsed = parse_acpx_output(stdout_text)

    assert parsed.format == expected_format
    assert len(parsed.events) == expected_event_count
