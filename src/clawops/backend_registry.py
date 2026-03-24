"""Strongclaw-owned ACP backend registry contracts."""

from __future__ import annotations

import dataclasses
from typing import Final

from clawops.orchestration import AuthMode
from clawops.platform_compat import DEFAULT_ACPX_VERSION, HostPlatform, build_compatibility_record

PINNED_ACPX_VERSION: Final[str] = DEFAULT_ACPX_VERSION


@dataclasses.dataclass(frozen=True, slots=True)
class BackendDefinition:
    """Resolved execution backend contract."""

    name: str
    agent_name: str
    supported_auth_modes: tuple[AuthMode, ...]
    default_auth_mode: AuthMode
    readiness_commands: tuple[tuple[str, ...], ...] = ()
    api_env_vars: tuple[str, ...] = ()
    cloud_env_vars: tuple[str, ...] = ()
    sanitized_env_by_mode: dict[AuthMode, tuple[str, ...]] = dataclasses.field(default_factory=dict)
    compatibility_version: str | None = None

    def supports_auth_mode(self, auth_mode: str) -> bool:
        """Return True when the backend supports *auth_mode*."""
        return auth_mode in self.supported_auth_modes


def _local_backend(name: str) -> BackendDefinition:
    """Return a registry entry for a local-auth backend."""
    return BackendDefinition(
        name=name,
        agent_name=name,
        supported_auth_modes=("local",),
        default_auth_mode="local",
        compatibility_version="vendor-managed",
    )


BACKEND_REGISTRY: Final[dict[str, BackendDefinition]] = {
    "codex": BackendDefinition(
        name="codex",
        agent_name="codex",
        supported_auth_modes=("subscription", "api"),
        default_auth_mode="subscription",
        readiness_commands=(("codex", "login", "status"),),
        api_env_vars=("OPENAI_API_KEY",),
        sanitized_env_by_mode={
            "subscription": ("OPENAI_API_KEY",),
            "api": (),
        },
        compatibility_version="vendor-managed",
    ),
    "claude": BackendDefinition(
        name="claude",
        agent_name="claude",
        supported_auth_modes=("subscription", "api", "cloud-provider"),
        default_auth_mode="subscription",
        readiness_commands=(
            ("claude", "auth", "status"),
            ("claude", "-p", "/status"),
        ),
        api_env_vars=("ANTHROPIC_API_KEY",),
        cloud_env_vars=(
            "AWS_PROFILE",
            "AWS_REGION",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "VERTEXAI_PROJECT",
        ),
        sanitized_env_by_mode={
            "subscription": ("ANTHROPIC_API_KEY",),
            "api": (),
            "cloud-provider": ("ANTHROPIC_API_KEY",),
        },
        compatibility_version="vendor-managed",
    ),
    "gemini": _local_backend("gemini"),
    "qwen": _local_backend("qwen"),
    "copilot": _local_backend("copilot"),
    "cursor": _local_backend("cursor"),
    "openclaw": _local_backend("openclaw"),
}


def resolve_backend(name: str) -> BackendDefinition:
    """Resolve one backend from the registry."""
    try:
        return BACKEND_REGISTRY[name]
    except KeyError as exc:
        supported = ", ".join(sorted(BACKEND_REGISTRY))
        raise KeyError(f"unknown backend {name!r}; supported backends: {supported}") from exc


def registry_contract() -> list[dict[str, object]]:
    """Return the backend registry as JSON-safe data."""
    records: list[dict[str, object]] = []
    for name in sorted(BACKEND_REGISTRY):
        backend = BACKEND_REGISTRY[name]
        records.append(
            {
                "name": backend.name,
                "agent_name": backend.agent_name,
                "supported_auth_modes": list(backend.supported_auth_modes),
                "default_auth_mode": backend.default_auth_mode,
                "readiness_commands": [list(command) for command in backend.readiness_commands],
                "compatibility_version": backend.compatibility_version,
            }
        )
    return records


def compatibility_matrix_fixture() -> dict[str, dict[str, object]]:
    """Return the initial macOS/Linux orchestration compatibility fixture."""
    return {
        "darwin-arm64": build_compatibility_record(HostPlatform("darwin", "arm64")),
        "linux-x86_64": build_compatibility_record(HostPlatform("linux", "x86_64")),
    }
