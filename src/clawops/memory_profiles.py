"""Shared StrongClaw memory profile registry."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryProfileSpec:
    """StrongClaw-managed OpenClaw memory profile."""

    profile_id: str
    render_profile: str
    description: str
    installs_qmd: bool = False
    installs_lossless_claw: bool = False
    installs_memory_pro: bool = False
    enables_hypermemory_backend: bool = False
    managed: bool = True


MEMORY_PROFILES: dict[str, MemoryProfileSpec] = {
    "hypermemory": MemoryProfileSpec(
        profile_id="hypermemory",
        render_profile="hypermemory",
        description="Default StrongClaw profile: lossless-claw + strongclaw-hypermemory.",
        installs_lossless_claw=True,
        enables_hypermemory_backend=True,
    ),
    "openclaw-default": MemoryProfileSpec(
        profile_id="openclaw-default",
        render_profile="openclaw-default",
        description="Built-in OpenClaw defaults: legacy context engine + memory-core.",
    ),
    "openclaw-qmd": MemoryProfileSpec(
        profile_id="openclaw-qmd",
        render_profile="openclaw-qmd",
        description="Built-in OpenClaw defaults plus the experimental QMD memory backend.",
        installs_qmd=True,
    ),
    "memory-lancedb-pro": MemoryProfileSpec(
        profile_id="memory-lancedb-pro",
        render_profile="memory-lancedb-pro",
        description="Vendored memory-lancedb-pro with Ollama-backed smart extraction.",
        installs_qmd=True,
        installs_memory_pro=True,
    ),
    "acp": MemoryProfileSpec(
        profile_id="acp",
        render_profile="acp",
        description="Legacy OpenClaw built-ins plus ACP worker agents.",
        installs_qmd=True,
        managed=False,
    ),
    "browser-lab": MemoryProfileSpec(
        profile_id="browser-lab",
        render_profile="browser-lab",
        description="Legacy OpenClaw built-ins plus browser-lab integration.",
        managed=False,
    ),
}

MANAGED_MEMORY_PROFILE_IDS: tuple[str, ...] = tuple(
    profile_id for profile_id, profile in MEMORY_PROFILES.items() if profile.managed
)


def resolve_memory_profile(profile_id: str) -> MemoryProfileSpec | None:
    """Resolve one profile from the shared registry."""
    return MEMORY_PROFILES.get(profile_id)


def require_memory_profile(profile_id: str) -> MemoryProfileSpec:
    """Resolve one managed profile and raise when unknown or unmanaged."""
    profile = resolve_memory_profile(profile_id)
    if profile is None or not profile.managed:
        available = ", ".join(sorted(MANAGED_MEMORY_PROFILE_IDS))
        raise ValueError(f"unknown memory profile: {profile_id} (choose from {available})")
    return profile
