"""Deterministic embedding provider for Qdrant-backed hypermemory tests."""

from __future__ import annotations

from collections.abc import Sequence

_TOPIC_TOKENS: tuple[tuple[str, ...], ...] = (
    ("gateway", "token", "credential", "rollover", "runbook"),
    ("deploy", "playbook", "cutovers"),
    ("alice",),
)


class DeterministicEmbeddingProvider:
    """Embed text into a tiny topic space so dense search results are predictable."""

    def embed_texts(
        self, texts: Sequence[str], *, timeout_ms: int | None = None
    ) -> list[list[float]]:
        """Return one normalized vector per text."""
        del timeout_ms
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        normalized = text.lower()
        vector = [
            1.0 if any(token in normalized for token in tokens) else 0.0 for tokens in _TOPIC_TOKENS
        ]
        if sum(value * value for value in vector) == 0.0:
            vector = [0.01, 0.01, 0.01]
        norm = sum(value * value for value in vector) ** 0.5
        return [value / norm for value in vector]
