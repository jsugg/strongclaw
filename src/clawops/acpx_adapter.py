"""Strongclaw-owned ACPX execution adapter boundary."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Literal

from clawops.process_runner import CommandResult, run_command

type ParsedOutputFormat = Literal["text", "json", "ndjson"]


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedAcpxOutput:
    """Structured ACPX stdout classification."""

    format: ParsedOutputFormat
    payload: object | None
    events: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "format": self.format,
            "payload": self.payload,
            "events": list(self.events),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class AcpxInvocation:
    """One ACPX execution request."""

    agent_name: str
    prompt: str
    cwd: pathlib.Path
    timeout_seconds: int
    no_wait: bool = False

    @property
    def command(self) -> list[str]:
        """Return the ACPX command line."""
        command = ["acpx", self.agent_name, "exec"]
        if self.no_wait:
            command.append("--no-wait")
        command.append(self.prompt)
        return command


@dataclasses.dataclass(frozen=True, slots=True)
class AcpxRunResult:
    """Combined subprocess and structured ACPX result."""

    invocation: AcpxInvocation
    command_result: CommandResult
    parsed_output: ParsedAcpxOutput


def parse_acpx_output(stdout_text: str) -> ParsedAcpxOutput:
    """Parse structured ACPX stdout when possible."""
    stripped = stdout_text.strip()
    if not stripped:
        return ParsedAcpxOutput(format="text", payload=None, events=())
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        return ParsedAcpxOutput(format="json", payload=payload, events=())

    events: list[dict[str, object]] = []
    for line in stripped.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return ParsedAcpxOutput(format="text", payload=None, events=())
        if not isinstance(payload, dict):
            return ParsedAcpxOutput(format="text", payload=None, events=())
        events.append(payload)
    return ParsedAcpxOutput(format="ndjson", payload=None, events=tuple(events))


class AcpxAdapter:
    """Execute ACPX commands through a stable Strongclaw boundary."""

    def run(
        self,
        invocation: AcpxInvocation,
        *,
        env: dict[str, str] | None = None,
    ) -> AcpxRunResult:
        """Run one ACPX invocation."""
        result = run_command(
            invocation.command,
            cwd=invocation.cwd,
            env=env,
            timeout_seconds=invocation.timeout_seconds,
        )
        parsed_output = parse_acpx_output(result.stdout)
        return AcpxRunResult(
            invocation=invocation,
            command_result=result,
            parsed_output=parsed_output,
        )
