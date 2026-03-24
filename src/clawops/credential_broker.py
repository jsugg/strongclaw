"""Policy-aware backend credential readiness checks."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from collections.abc import Mapping
from typing import Final, Literal, cast

from clawops.backend_registry import BackendDefinition, resolve_backend
from clawops.common import dump_json
from clawops.orchestration import AUTH_MODES, AuthMode
from clawops.process_runner import run_command

type CredentialState = Literal[
    "ready",
    "bootstrap_required",
    "expired",
    "misconfigured",
    "forbidden_by_policy",
]

READY_STATES: Final[frozenset[str]] = frozenset({"ready"})
READY_TEXT_TOKENS: Final[tuple[str, ...]] = (
    "logged in",
    "authenticated",
    "auth method",
    "claude.ai",
    "chatgpt",
)
BOOTSTRAP_TEXT_TOKENS: Final[tuple[str, ...]] = (
    "not logged in",
    "login required",
    "sign in",
    "reauthenticate",
)
EXPIRED_TEXT_TOKENS: Final[tuple[str, ...]] = ("expired", "token expired", "session expired")


@dataclasses.dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Machine-readable credential readiness result."""

    backend: str
    auth_mode: AuthMode
    state: CredentialState
    source_class: str
    message: str
    removed_env_keys: tuple[str, ...]
    readiness_command: tuple[str, ...] | None = None
    metadata: Mapping[str, object] = dataclasses.field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """Return True when the selected credentials are ready for dispatch."""
        return self.state in READY_STATES

    def with_state(self, state: CredentialState, message: str) -> "CredentialStatus":
        """Return a copy with a different readiness state."""
        return dataclasses.replace(self, state=state, message=message)

    def sanitized_env(self, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return an environment with conflicting auth variables removed."""
        base_env = dict(os.environ if environ is None else environ)
        for key in self.removed_env_keys:
            base_env.pop(key, None)
        return base_env

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe credential status record."""
        return {
            "backend": self.backend,
            "auth_mode": self.auth_mode,
            "state": self.state,
            "source_class": self.source_class,
            "message": self.message,
            "removed_env_keys": list(self.removed_env_keys),
            "readiness_command": list(self.readiness_command) if self.readiness_command else None,
            "metadata": dict(self.metadata),
        }


def _classify_text_status(text: str) -> CredentialState:
    """Classify a human-readable readiness response conservatively."""
    normalized = text.casefold()
    if any(token in normalized for token in EXPIRED_TEXT_TOKENS):
        return "expired"
    if any(token in normalized for token in BOOTSTRAP_TEXT_TOKENS):
        return "bootstrap_required"
    if any(token in normalized for token in READY_TEXT_TOKENS):
        return "ready"
    return "misconfigured"


def _classify_json_status(payload: object) -> CredentialState:
    """Classify a structured readiness response conservatively."""
    if isinstance(payload, dict):
        lowered = {str(key).casefold(): value for key, value in payload.items()}
        for key in ("authenticated", "ready", "loggedin", "signedin"):
            value = lowered.get(key)
            if isinstance(value, bool):
                return "ready" if value else "bootstrap_required"
        status_value = lowered.get("status")
        if isinstance(status_value, str):
            normalized = status_value.casefold()
            if normalized in {"authenticated", "ready", "ok", "active"}:
                return "ready"
            if normalized in {"expired", "token_expired"}:
                return "expired"
            if normalized in {"not_authenticated", "login_required", "logged_out"}:
                return "bootstrap_required"
    return "misconfigured"


def _probe_command(command: tuple[str, ...]) -> tuple[CredentialState, str, Mapping[str, object]]:
    """Run one readiness command and classify its result."""
    result = run_command(list(command), timeout_seconds=15)
    combined_output = "\n".join(
        part for part in (result.stdout.strip(), result.stderr.strip()) if part
    ).strip()
    if result.failed_to_start:
        return "misconfigured", combined_output or "command failed to start", {}
    parsed: object | None = None
    if combined_output:
        try:
            parsed = json.loads(combined_output)
        except json.JSONDecodeError:
            parsed = None
    state = (
        _classify_json_status(parsed)
        if parsed is not None
        else _classify_text_status(combined_output or "status command returned no output")
    )
    if result.returncode not in {0, None} and state == "ready":
        state = "misconfigured"
    metadata = parsed if isinstance(parsed, dict) else {}
    return state, combined_output or "status command returned no output", metadata


def _sanitize_env_keys(definition: BackendDefinition, auth_mode: AuthMode) -> tuple[str, ...]:
    """Return the env keys that must be removed before dispatch."""
    return tuple(sorted(set(definition.sanitized_env_by_mode.get(auth_mode, ()))))


def _probe_selected_mode(
    definition: BackendDefinition,
    auth_mode: AuthMode,
    *,
    environ: Mapping[str, str],
) -> CredentialStatus:
    """Probe the selected auth mode without policy fallback handling."""
    removed_env_keys = _sanitize_env_keys(definition, auth_mode)
    if auth_mode == "local":
        return CredentialStatus(
            backend=definition.name,
            auth_mode=auth_mode,
            state="ready",
            source_class="local-runtime",
            message="local auth backend requires no credential probe",
            removed_env_keys=removed_env_keys,
        )
    if auth_mode == "api":
        present_keys = [key for key in definition.api_env_vars if environ.get(key)]
        state: CredentialState = "ready" if present_keys else "misconfigured"
        message = (
            f"API credentials available via {', '.join(present_keys)}"
            if present_keys
            else "required API credentials are not present in the environment"
        )
        return CredentialStatus(
            backend=definition.name,
            auth_mode=auth_mode,
            state=state,
            source_class="process-env",
            message=message,
            removed_env_keys=removed_env_keys,
            metadata={"present_env_keys": present_keys},
        )
    if auth_mode == "cloud-provider":
        present_keys = [key for key in definition.cloud_env_vars if environ.get(key)]
        state = "ready" if present_keys else "misconfigured"
        message = (
            f"cloud-provider credentials available via {', '.join(present_keys)}"
            if present_keys
            else "required cloud-provider credentials are not present in the environment"
        )
        return CredentialStatus(
            backend=definition.name,
            auth_mode=auth_mode,
            state=state,
            source_class="cloud-env",
            message=message,
            removed_env_keys=removed_env_keys,
            metadata={"present_env_keys": present_keys},
        )

    for command in definition.readiness_commands:
        state, message, metadata = _probe_command(command)
        if state == "misconfigured" and "not found" in message.casefold():
            continue
        return CredentialStatus(
            backend=definition.name,
            auth_mode=auth_mode,
            state=state,
            source_class="vendor-cli-state",
            message=message,
            removed_env_keys=removed_env_keys,
            readiness_command=command,
            metadata=metadata,
        )
    return CredentialStatus(
        backend=definition.name,
        auth_mode=auth_mode,
        state="misconfigured",
        source_class="vendor-cli-state",
        message="no supported readiness command is available on PATH",
        removed_env_keys=removed_env_keys,
    )


class CredentialBroker:
    """Resolve and validate backend credential readiness."""

    def evaluate(
        self,
        backend: str,
        *,
        required_auth_mode: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> CredentialStatus:
        """Return the credential readiness status for one backend."""
        definition = resolve_backend(backend)
        selected_mode = (
            definition.default_auth_mode if required_auth_mode is None else required_auth_mode
        )
        if selected_mode not in AUTH_MODES:
            allowed_modes = ", ".join(sorted(AUTH_MODES))
            raise ValueError(f"required_auth_mode must be one of: {allowed_modes}")
        auth_mode = cast(AuthMode, selected_mode)
        if not definition.supports_auth_mode(auth_mode):
            return CredentialStatus(
                backend=definition.name,
                auth_mode=auth_mode,
                state="forbidden_by_policy",
                source_class="policy",
                message=f"backend {definition.name} does not support auth mode {auth_mode}",
                removed_env_keys=(),
            )
        effective_env = dict(os.environ if environ is None else environ)
        selected = _probe_selected_mode(definition, auth_mode, environ=effective_env)
        if selected.ready:
            return selected

        for alternate_mode in definition.supported_auth_modes:
            if alternate_mode == auth_mode:
                continue
            alternate = _probe_selected_mode(definition, alternate_mode, environ=effective_env)
            if alternate.ready:
                return selected.with_state(
                    "forbidden_by_policy",
                    (
                        f"required auth mode {auth_mode} is not ready; "
                        f"only {alternate_mode} credentials are available"
                    ),
                )
        return selected


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse credential broker CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--auth-mode")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    status = CredentialBroker().evaluate(args.backend, required_auth_mode=args.auth_mode)
    print(dump_json(status.to_dict()).rstrip())
    return 0 if status.ready else 1
