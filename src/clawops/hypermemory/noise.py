"""Noise filtering helpers for the hypermemory write pipeline."""

from __future__ import annotations

import re
from collections.abc import Sequence

from clawops.hypermemory.models import NoiseConfig

NOISE_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"(?i)I don'?t (have|recall|remember|know)\b"),
    re.compile(r"(?i)no (relevant|matching) memories?\b"),
    re.compile(r"(?i)I couldn'?t find\b"),
    re.compile(r"(?i)^(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you)\s*[!.]?$"),
    re.compile(r"(?i)^(fresh session|new conversation|heartbeat|ping)\s*$"),
    re.compile(r"(?i)query\s*->\s*none"),
    re.compile(r"(?i)no explicit solution"),
    re.compile(r"^```"),
    re.compile(r"^\{[\s\S]*\}$"),
)


def is_noise(text: str, *, config: NoiseConfig | None = None) -> bool:
    """Return whether *text* should be rejected as durable memory noise."""
    thresholds = config or NoiseConfig()
    stripped = text.strip()
    if len(stripped) < thresholds.min_text_length:
        return True
    if len(stripped) > thresholds.max_text_length:
        return True
    return any(pattern.search(stripped) for pattern in NOISE_PATTERNS)
