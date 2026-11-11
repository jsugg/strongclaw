"""Redact generated env-contract secrets from anything the harness records or uploads."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from clawops.strongclaw_runtime import load_env_assignments

REDACTED: Final[str] = "[REDACTED]"
_SECRET_KEY_MARKERS: Final[tuple[str, ...]] = ("TOKEN", "KEY", "PASSWORD", "SECRET")
_MIN_SECRET_LENGTH: Final[int] = 8


def secret_values(env_file: Path) -> tuple[str, ...]:
    """Return secret-looking values from an env contract, longest first."""
    values = load_env_assignments(env_file) if env_file.is_file() else {}
    return tuple(
        sorted(
            {
                value
                for key, value in values.items()
                if any(marker in key for marker in _SECRET_KEY_MARKERS)
                and len(value) >= _MIN_SECRET_LENGTH
            },
            key=len,
            reverse=True,
        )
    )


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """Replace every known secret value in *text*."""
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text
