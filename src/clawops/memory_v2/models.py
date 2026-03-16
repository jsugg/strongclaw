"""Shared data models for the strongclaw memory v2 engine."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Sequence
from typing import Any, Literal

DEFAULT_MEMORY_FILE_NAMES = ("MEMORY.md", "memory.md")
DEFAULT_DAILY_DIR = "memory"
DEFAULT_BANK_DIR = "bank"
DEFAULT_DB_PATH = ".openclaw/memory-v2.sqlite"
DEFAULT_SNIPPET_CHARS = 400
DEFAULT_SEARCH_RESULTS = 8
DEFAULT_DEFAULT_SCOPE = "project:strongclaw"
DEFAULT_READABLE_SCOPE_PATTERNS = ("project:", "agent:", "user:", "global")
DEFAULT_WRITABLE_SCOPE_PATTERNS = ("project:", "agent:")
DEFAULT_AUTO_APPLY_SCOPE_PATTERNS = ("project:", "agent:")

Lane = Literal["memory", "corpus"]
SearchMode = Literal["all", "memory", "corpus"]
EntryType = Literal[
    "fact",
    "reflection",
    "opinion",
    "entity",
    "proposal",
    "paragraph",
    "section",
]
ReflectionMode = Literal["safe", "propose", "apply"]


@dataclasses.dataclass(frozen=True, slots=True)
class CorpusPathConfig:
    """Additional Markdown corpus path to index."""

    name: str
    path: pathlib.Path
    pattern: str


@dataclasses.dataclass(frozen=True, slots=True)
class GovernanceConfig:
    """Scope and write-governance configuration."""

    default_scope: str
    readable_scope_patterns: tuple[str, ...]
    writable_scope_patterns: tuple[str, ...]
    auto_apply_scope_patterns: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class RankingConfig:
    """Search ranking configuration."""

    memory_lane_weight: float = 1.0
    corpus_lane_weight: float = 1.0
    lexical_weight: float = 0.75
    coverage_weight: float = 0.35
    confidence_weight: float = 0.15
    recency_weight: float = 0.1
    contradiction_penalty: float = 0.2
    diversity_penalty: float = 0.35
    recency_half_life_days: int = 45


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryV2Config:
    """Validated memory-v2 configuration."""

    config_path: pathlib.Path
    workspace_root: pathlib.Path
    db_path: pathlib.Path
    memory_file_names: tuple[str, ...]
    daily_dir: str
    bank_dir: str
    include_default_memory: bool
    corpus_paths: tuple[CorpusPathConfig, ...]
    max_snippet_chars: int
    default_max_results: int
    governance: GovernanceConfig
    ranking: RankingConfig

    @property
    def proposals_path(self) -> pathlib.Path:
        """Return the canonical proposal log path."""
        return self.workspace_root / self.bank_dir / "proposals.md"


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedItem:
    """Single indexed search item."""

    item_type: EntryType
    title: str
    snippet: str
    start_line: int
    end_line: int
    scope: str
    confidence: float | None = None
    entities: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    proposal_id: str | None = None
    proposal_status: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class IndexedDocument:
    """Materialized document ready to persist into the derived index."""

    rel_path: str
    abs_path: pathlib.Path
    lane: Lane
    source_name: str
    sha256: str
    line_count: int
    modified_at: str
    items: tuple[ParsedItem, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SearchExplanation:
    """Explainable ranking components for a search hit."""

    lexical_score: float
    lane_weight: float
    type_weight: float
    coverage_boost: float
    confidence_boost: float
    recency_boost: float
    contradiction_penalty: float
    novelty_penalty: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Convert the explanation to a serializable mapping."""
        return {
            "lexicalScore": round(self.lexical_score, 6),
            "laneWeight": round(self.lane_weight, 6),
            "typeWeight": round(self.type_weight, 6),
            "coverageBoost": round(self.coverage_boost, 6),
            "confidenceBoost": round(self.confidence_boost, 6),
            "recencyBoost": round(self.recency_boost, 6),
            "contradictionPenalty": round(self.contradiction_penalty, 6),
            "noveltyPenalty": round(self.novelty_penalty, 6),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class SearchHit:
    """Search result payload compatible with OpenClaw memory tools."""

    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    source: Literal["memory"] = "memory"
    lane: Lane = "memory"
    item_type: str = "paragraph"
    confidence: float | None = None
    entities: tuple[str, ...] = ()
    scope: str | None = None
    evidence_count: int = 0
    contradiction_count: int = 0
    explanation: SearchExplanation | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the hit to a serializable dictionary."""
        payload: dict[str, Any] = {
            "path": self.path,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "score": round(self.score, 6),
            "snippet": self.snippet,
            "source": self.source,
            "lane": self.lane,
            "itemType": self.item_type,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.entities:
            payload["entities"] = list(self.entities)
        if self.scope:
            payload["scope"] = self.scope
        if self.evidence_count:
            payload["evidenceCount"] = self.evidence_count
        if self.contradiction_count:
            payload["contradictionCount"] = self.contradiction_count
        if self.explanation is not None:
            payload["explain"] = self.explanation.to_dict()
        return payload


@dataclasses.dataclass(frozen=True, slots=True)
class ReindexSummary:
    """Summary of a reindex run."""

    files: int
    chunks: int
    dirty: bool
    facts: int = 0
    opinions: int = 0
    reflections: int = 0
    entities: int = 0
    proposals: int = 0

    def to_dict(self) -> dict[str, int | bool]:
        """Convert the summary to a serializable dictionary."""
        return {
            "files": self.files,
            "chunks": self.chunks,
            "dirty": self.dirty,
            "facts": self.facts,
            "opinions": self.opinions,
            "reflections": self.reflections,
            "entities": self.entities,
            "proposals": self.proposals,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalRecord:
    """Canonical proposal derived from retained notes."""

    proposal_id: str
    kind: Literal["fact", "reflection", "opinion", "entity"]
    entry_line: str
    scope: str
    source_rel_path: str
    source_line: int
    status: Literal["pending", "applied"]
    entity: str | None = None
    confidence: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ReflectionSummary:
    """Reflection result payload."""

    proposed: int
    applied: int
    pending: int
    reflected: dict[str, int]
    index: ReindexSummary

    def to_dict(self) -> dict[str, Any]:
        """Convert the reflection summary to a serializable dictionary."""
        return {
            "ok": True,
            "proposed": self.proposed,
            "applied": self.applied,
            "pending": self.pending,
            "reflected": dict(self.reflected),
            "index": self.index.to_dict(),
        }


TYPE_ORDER: dict[EntryType, float] = {
    "fact": 1.15,
    "entity": 1.1,
    "opinion": 1.05,
    "reflection": 1.0,
    "proposal": 0.98,
    "paragraph": 0.9,
    "section": 0.8,
}


def normalize_text_tokens(text: str) -> tuple[str, ...]:
    """Return normalized lowercase tokens for similarity and coverage checks."""
    collapsed = "".join(character.lower() if character.isalnum() else " " for character in text)
    return tuple(token for token in collapsed.split() if token)


def evidence_labels(evidence: Sequence[str]) -> tuple[str, ...]:
    """Return deterministic evidence labels."""
    return tuple(sorted({entry.strip() for entry in evidence if entry.strip()}))
