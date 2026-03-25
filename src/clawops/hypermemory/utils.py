"""Pure helper utilities for StrongClaw hypermemory.

These functions are intentionally stateless and side-effect free.

They exist to support the ongoing composition refactor: both the engine and
service objects can use these helpers without creating cross-service
dependencies.
"""

from __future__ import annotations

import hashlib


def sha256(value: str) -> str:
    """Return a SHA-256 hex digest for *value*."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    """Return a stable slug for an entity file path."""
    lowered = value.strip().lower()
    slug = "".join(character if character.isalnum() else "-" for character in lowered)
    collapsed = "-".join(part for part in slug.split("-") if part)
    return collapsed or "entity"


def normalize_text(text: str) -> tuple[str, ...]:
    """Normalize text into lowercase search tokens."""
    collapsed = "".join(character.lower() if character.isalnum() else " " for character in text)
    return tuple(token for token in collapsed.split() if token)


def normalized_retrieval_text(title: str, snippet: str) -> str:
    """Return normalized text used for lexical and dense retrieval."""
    combined = f"{title} {snippet}"
    return " ".join(token for token in normalize_text(combined))


def point_id(
    *,
    document_rel_path: str,
    item_type: str,
    start_line: int,
    end_line: int,
    snippet: str,
) -> str:
    """Return a stable point identifier for a search item."""
    digest = sha256(f"{document_rel_path}:{item_type}:{start_line}:{end_line}:{snippet.strip()}")[
        :32
    ]
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
