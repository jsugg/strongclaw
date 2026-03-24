"""Markdown parsing for StrongClaw hypermemory."""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from typing import cast

from clawops.hypermemory.config import resolve_under_workspace
from clawops.hypermemory.models import (
    EntryType,
    IndexedDocument,
    Lane,
    ParsedItem,
    Tier,
    evidence_labels,
)

HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")
ENTITY_TAG_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_.-]*)")
RETAIN_HEADER_RE = re.compile(r"^#{1,6}\s+retain\s*$", re.IGNORECASE)
TYPED_PREFIX_RE = re.compile(r"^(?P<kind>[A-Za-z]+)(?:\[(?P<meta>[^\]]+)\])?:\s*(?P<text>.+?)\s*$")
VALID_TIERS = {"core", "working", "peripheral"}


@dataclasses.dataclass(frozen=True, slots=True)
class TypedEntry:
    """Parsed typed entry payload."""

    item_type: EntryType
    entry_line: str
    scope: str
    confidence: float | None
    entities: tuple[str, ...]
    evidence: tuple[str, ...]
    contradicts: tuple[str, ...]
    proposal_id: str | None = None
    proposal_status: str | None = None
    importance: float | None = None
    tier: Tier = "working"
    access_count: int = 0
    last_access_date: str | None = None
    injected_count: int = 0
    confirmed_count: int = 0
    bad_recall_count: int = 0
    fact_key: str | None = None
    invalidated_at: str | None = None
    supersedes: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RetainedNote:
    """Typed note extracted from a retain section."""

    kind: EntryType
    entry_line: str
    scope: str
    source_line: int
    confidence: float | None = None
    entity: str | None = None


def build_document(
    *,
    workspace_root: pathlib.Path,
    path: pathlib.Path,
    lane: Lane,
    source_name: str,
    default_scope: str,
) -> IndexedDocument:
    """Build an indexed document from a canonical Markdown path."""
    rel_path = resolve_under_workspace(workspace_root, path)
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    items = tuple(parse_markdown(lines, default_scope=default_scope))
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    return IndexedDocument(
        rel_path=rel_path,
        abs_path=path.resolve(),
        lane=lane,  # type: ignore[arg-type]
        source_name=source_name,
        sha256=sha256,
        line_count=len(lines),
        modified_at=modified_at,
        items=items,
    )


def parse_markdown(lines: Sequence[str], *, default_scope: str) -> Iterator[ParsedItem]:
    """Split Markdown into searchable paragraphs and typed entries."""
    headings: list[str] = []
    paragraph_lines: list[str] = []
    paragraph_start = 1

    def flush_paragraph(end_line: int) -> Iterator[ParsedItem]:
        if not paragraph_lines:
            return
        snippet = " ".join(line.strip() for line in paragraph_lines if line.strip()).strip()
        if snippet:
            title = " / ".join(headings) if headings else "Document"
            yield ParsedItem(
                item_type="paragraph",
                title=title,
                snippet=snippet,
                start_line=paragraph_start,
                end_line=end_line,
                scope=default_scope,
                entities=tuple(sorted(set(ENTITY_TAG_RE.findall(snippet)))),
            )
        paragraph_lines.clear()

    for line_number, raw_line in enumerate(lines, start=1):
        heading_match = HEADING_RE.match(raw_line)
        if heading_match:
            yield from flush_paragraph(line_number - 1)
            level = len(heading_match.group("level"))
            title = heading_match.group("title").strip()
            headings[:] = headings[: level - 1]
            headings.append(title)
            yield ParsedItem(
                item_type="section",
                title=" / ".join(headings),
                snippet=title,
                start_line=line_number,
                end_line=line_number,
                scope=default_scope,
            )
            continue
        bullet_match = BULLET_RE.match(raw_line)
        if bullet_match:
            yield from flush_paragraph(line_number - 1)
            body = bullet_match.group("body").strip()
            typed = parse_typed_entry(body, default_scope=default_scope)
            title = " / ".join(headings) if headings else "Document"
            if typed is not None:
                yield ParsedItem(
                    item_type=typed.item_type,
                    title=title,
                    snippet=typed.entry_line,
                    start_line=line_number,
                    end_line=line_number,
                    scope=typed.scope,
                    confidence=typed.confidence,
                    entities=typed.entities,
                    evidence=evidence_labels(typed.evidence),
                    contradicts=typed.contradicts,
                    proposal_id=typed.proposal_id,
                    proposal_status=typed.proposal_status,
                    importance=typed.importance,
                    tier=typed.tier,
                    access_count=typed.access_count,
                    last_access_date=typed.last_access_date,
                    injected_count=typed.injected_count,
                    confirmed_count=typed.confirmed_count,
                    bad_recall_count=typed.bad_recall_count,
                    fact_key=typed.fact_key,
                    invalidated_at=typed.invalidated_at,
                    supersedes=typed.supersedes,
                )
            else:
                yield ParsedItem(
                    item_type="paragraph",
                    title=title,
                    snippet=body,
                    start_line=line_number,
                    end_line=line_number,
                    scope=default_scope,
                    entities=tuple(sorted(set(ENTITY_TAG_RE.findall(body)))),
                )
            continue
        if not raw_line.strip():
            yield from flush_paragraph(line_number - 1)
            paragraph_start = line_number + 1
            continue
        if not paragraph_lines:
            paragraph_start = line_number
        paragraph_lines.append(raw_line)
    yield from flush_paragraph(len(lines))


def parse_typed_entry(body: str, *, default_scope: str) -> TypedEntry | None:
    """Parse a typed bullet entry with optional metadata."""
    match = TYPED_PREFIX_RE.match(body.strip())
    if not match:
        return None
    raw_kind = match.group("kind").strip().lower()
    if raw_kind not in {"fact", "reflection", "opinion", "entity", "proposal"}:
        return None
    metadata = _parse_metadata(raw_kind, match.group("meta"))
    text = match.group("text").strip()
    scope = str(metadata.pop("scope", default_scope)).strip()
    confidence = _as_confidence(metadata.pop("c", metadata.pop("confidence", None)))
    contradicts = _as_string_tuple(metadata.pop("contradicts", metadata.pop("conflict", None)))
    evidence = _as_string_tuple(metadata.pop("evidence", None))
    importance = _as_importance(metadata.pop("importance", None))
    tier = _as_tier(metadata.pop("tier", None))
    access_count = _as_non_negative_int(
        metadata.pop("accessed", metadata.pop("access_count", None))
    )
    last_access_date = _as_iso_date(
        metadata.pop("last_access", metadata.pop("last_access_date", None))
    )
    injected_count = _as_non_negative_int(
        metadata.pop("injected", metadata.pop("injected_count", None))
    )
    confirmed_count = _as_non_negative_int(
        metadata.pop("confirmed", metadata.pop("confirmed_count", None))
    )
    bad_recall_count = _as_non_negative_int(
        metadata.pop("bad_recall", metadata.pop("bad_recall_count", None))
    )
    fact_key = _as_optional_string(metadata.pop("fact_key", None))
    invalidated_at = _as_iso_date(metadata.pop("invalidated", metadata.pop("invalidated_at", None)))
    supersedes = _as_optional_string(metadata.pop("supersedes", None))
    entities = set(ENTITY_TAG_RE.findall(text))
    if raw_kind == "entity":
        entity_name = str(metadata.pop("entity", metadata.pop("name", text))).strip()
        entities.add(entity_name)
    if raw_kind == "proposal":
        return TypedEntry(
            item_type="proposal",
            entry_line=body.strip(),
            scope=scope,
            confidence=confidence,
            entities=tuple(sorted(entities)),
            evidence=evidence,
            contradicts=contradicts,
            proposal_id=str(metadata.get("id", "")).strip() or None,
            proposal_status=str(metadata.get("status", "")).strip() or None,
            importance=importance,
            tier=tier,
            access_count=access_count,
            last_access_date=last_access_date,
            injected_count=injected_count,
            confirmed_count=confirmed_count,
            bad_recall_count=bad_recall_count,
            fact_key=fact_key,
            invalidated_at=invalidated_at,
            supersedes=supersedes,
        )
    if raw_kind == "fact":
        entry_line = _format_typed_entry(
            "Fact",
            text=text,
            scope=scope,
            evidence=evidence,
            contradicts=contradicts,
            importance=importance,
            tier=tier,
            access_count=access_count,
            last_access_date=last_access_date,
            injected_count=injected_count,
            confirmed_count=confirmed_count,
            bad_recall_count=bad_recall_count,
            fact_key=fact_key,
            invalidated_at=invalidated_at,
            supersedes=supersedes,
        )
    elif raw_kind == "reflection":
        entry_line = _format_typed_entry(
            "Reflection",
            text=text,
            scope=scope,
            evidence=evidence,
            contradicts=contradicts,
            importance=importance,
            tier=tier,
            access_count=access_count,
            last_access_date=last_access_date,
            injected_count=injected_count,
            confirmed_count=confirmed_count,
            bad_recall_count=bad_recall_count,
            fact_key=fact_key,
            invalidated_at=invalidated_at,
            supersedes=supersedes,
        )
    elif raw_kind == "opinion":
        entry_line = _format_typed_entry(
            "Opinion",
            text=text,
            scope=scope,
            confidence=confidence,
            evidence=evidence,
            contradicts=contradicts,
            importance=importance,
            tier=tier,
            access_count=access_count,
            last_access_date=last_access_date,
            injected_count=injected_count,
            confirmed_count=confirmed_count,
            bad_recall_count=bad_recall_count,
            fact_key=fact_key,
            invalidated_at=invalidated_at,
            supersedes=supersedes,
        )
    else:
        entity_name = str(metadata.get("entity", metadata.get("name", text))).strip()
        entry_line = _format_typed_entry(
            "Entity",
            text=text,
            scope=scope,
            entity=entity_name,
            evidence=evidence,
            contradicts=contradicts,
            importance=importance,
            tier=tier,
            access_count=access_count,
            last_access_date=last_access_date,
            injected_count=injected_count,
            confirmed_count=confirmed_count,
            bad_recall_count=bad_recall_count,
            fact_key=fact_key,
            invalidated_at=invalidated_at,
            supersedes=supersedes,
        )
        entities.add(entity_name)
    return TypedEntry(
        item_type=cast(EntryType, raw_kind),
        entry_line=entry_line,
        scope=scope,
        confidence=confidence,
        entities=tuple(sorted(entities)),
        evidence=evidence,
        contradicts=contradicts,
        importance=importance,
        tier=tier,
        access_count=access_count,
        last_access_date=last_access_date,
        injected_count=injected_count,
        confirmed_count=confirmed_count,
        bad_recall_count=bad_recall_count,
        fact_key=fact_key,
        invalidated_at=invalidated_at,
        supersedes=supersedes,
    )


def iter_retained_notes(
    path: pathlib.Path,
    *,
    default_scope: str,
) -> Iterator[RetainedNote]:
    """Yield typed retain bullets from a daily log file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    in_retain = False
    for line_number, raw_line in enumerate(lines, start=1):
        if RETAIN_HEADER_RE.match(raw_line.strip()):
            in_retain = True
            continue
        if in_retain and raw_line.startswith("#"):
            in_retain = False
            continue
        if not in_retain:
            continue
        bullet_match = BULLET_RE.match(raw_line)
        if not bullet_match:
            continue
        body = bullet_match.group("body").strip()
        parsed = parse_typed_entry(body, default_scope=default_scope)
        if parsed is None:
            entry_line = _format_typed_entry("Fact", text=body, scope=default_scope)
            yield RetainedNote(
                kind="fact",
                entry_line=entry_line,
                scope=default_scope,
                confidence=None,
                entity=None,
                source_line=line_number,
            )
            continue
        yield RetainedNote(
            kind=parsed.item_type,
            entry_line=parsed.entry_line,
            scope=parsed.scope,
            confidence=parsed.confidence,
            entity=next(iter(parsed.entities), None),
            source_line=line_number,
        )


def _parse_metadata(kind: str, raw_meta: str | None) -> dict[str, object]:
    """Parse typed-entry metadata inside square brackets."""
    if raw_meta is None or not raw_meta.strip():
        return {}
    parsed: dict[str, object] = {}
    tokens = _split_metadata_tokens(raw_meta)
    for index, token in enumerate(tokens):
        piece = token.strip()
        if not piece:
            continue
        if "=" in piece:
            key, value = piece.split("=", 1)
            parsed[key.strip().lower()] = value.strip()
            continue
        if ":" in piece:
            key, value = piece.split(":", 1)
            parsed[key.strip().lower()] = value.strip()
            continue
        if kind == "entity" and index == 0:
            parsed["entity"] = piece
            continue
        if kind == "opinion" and index == 0:
            parsed["c"] = piece
            continue
        parsed[piece.lower()] = True
    return parsed


def _as_confidence(value: object) -> float | None:
    """Parse an optional confidence score."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError("confidence must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as err:
        raise TypeError("confidence must be numeric") from err
    if not 0.0 <= numeric <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return numeric


def _as_string_tuple(value: object) -> tuple[str, ...]:
    """Parse comma- or pipe-delimited metadata values into a tuple."""
    if value is None:
        return ()
    if not isinstance(value, str):
        raise TypeError("metadata values must be strings")
    separators = "," if "," in value else "|"
    return tuple(part.strip() for part in value.split(separators) if part.strip())


def _format_typed_entry(
    kind: str,
    *,
    text: str,
    scope: str,
    confidence: float | None = None,
    entity: str | None = None,
    evidence: tuple[str, ...] = (),
    contradicts: tuple[str, ...] = (),
    importance: float | None = None,
    tier: Tier = "working",
    access_count: int = 0,
    last_access_date: str | None = None,
    injected_count: int = 0,
    confirmed_count: int = 0,
    bad_recall_count: int = 0,
    fact_key: str | None = None,
    invalidated_at: str | None = None,
    supersedes: str | None = None,
) -> str:
    """Format a canonical typed entry line."""
    metadata: list[str] = []
    if kind == "Entity" and entity:
        metadata.append(entity.strip())
    if kind == "Opinion" and confidence is not None:
        metadata.append(f"c={confidence:.2f}")
    metadata.append(f"scope={scope}")
    if importance is not None:
        metadata.append(f"importance={importance:.2f}")
    if tier != "working":
        metadata.append(f"tier={tier}")
    if access_count > 0:
        metadata.append(f"accessed={access_count}")
    if last_access_date:
        metadata.append(f"last_access={last_access_date}")
    if injected_count > 0:
        metadata.append(f"injected={injected_count}")
    if confirmed_count > 0:
        metadata.append(f"confirmed={confirmed_count}")
    if bad_recall_count > 0:
        metadata.append(f"bad_recall={bad_recall_count}")
    if fact_key:
        metadata.append(f"fact_key={fact_key}")
    if invalidated_at:
        metadata.append(f"invalidated={invalidated_at}")
    if supersedes:
        metadata.append(f"supersedes={supersedes}")
    if evidence:
        metadata.append(f"evidence={'|'.join(evidence)}")
    if contradicts:
        metadata.append(f"contradicts={'|'.join(contradicts)}")
    return f"{kind}[{','.join(metadata)}]: {text.strip()}"


def _split_metadata_tokens(raw_meta: str) -> list[str]:
    """Split metadata on commas, falling back to whitespace-separated tokens."""
    if "," in raw_meta:
        return [token.strip() for token in raw_meta.split(",")]
    return [token.strip() for token in raw_meta.split()]


def _as_importance(value: object) -> float | None:
    """Parse an optional importance score."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        converted = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            converted = float(stripped)
        except ValueError:
            return None
    else:
        return None
    if 0.0 <= converted <= 1.0:
        return converted
    return None


def _as_tier(value: object) -> Tier:
    """Parse a lifecycle tier with a safe default."""
    if not isinstance(value, str):
        return "working"
    normalized = value.strip().lower()
    if normalized in VALID_TIERS:
        return cast(Tier, normalized)
    return "working"


def _as_non_negative_int(value: object) -> int:
    """Parse a non-negative integer with a safe default."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            parsed = int(stripped)
        except ValueError:
            return 0
        return max(parsed, 0)
    return 0


def _as_iso_date(value: object) -> str | None:
    """Parse an ISO date string."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return date.fromisoformat(stripped).isoformat()
    except ValueError:
        return None


def _as_optional_string(value: object) -> str | None:
    """Normalize an optional string value."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
