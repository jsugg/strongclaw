"""Markdown parsing for strongclaw memory v2."""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import cast

from clawops.memory_v2.config import resolve_under_workspace
from clawops.memory_v2.models import EntryType, IndexedDocument, Lane, ParsedItem, evidence_labels

HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")
ENTITY_TAG_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_.-]*)")
RETAIN_HEADER_RE = re.compile(r"^#{1,6}\s+retain\s*$", re.IGNORECASE)
TYPED_PREFIX_RE = re.compile(r"^(?P<kind>[A-Za-z]+)(?:\[(?P<meta>[^\]]+)\])?:\s*(?P<text>.+?)\s*$")


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
        )
    if raw_kind == "fact":
        entry_line = _format_typed_entry("Fact", text=text, scope=scope)
    elif raw_kind == "reflection":
        entry_line = _format_typed_entry("Reflection", text=text, scope=scope)
    elif raw_kind == "opinion":
        entry_line = _format_typed_entry("Opinion", text=text, scope=scope, confidence=confidence)
    else:
        entity_name = str(metadata.get("entity", metadata.get("name", text))).strip()
        entry_line = _format_typed_entry("Entity", text=text, scope=scope, entity=entity_name)
        entities.add(entity_name)
    return TypedEntry(
        item_type=cast(EntryType, raw_kind),
        entry_line=entry_line,
        scope=scope,
        confidence=confidence,
        entities=tuple(sorted(entities)),
        evidence=evidence,
        contradicts=contradicts,
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
    for index, token in enumerate(raw_meta.split(",")):
        piece = token.strip()
        if not piece:
            continue
        if "=" in piece:
            key, value = piece.split("=", 1)
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
) -> str:
    """Format a canonical typed entry line."""
    metadata: list[str] = []
    if kind == "Entity" and entity:
        metadata.append(entity.strip())
    if kind == "Opinion" and confidence is not None:
        metadata.append(f"c={confidence:.2f}")
    metadata.append(f"scope={scope}")
    return f"{kind}[{','.join(metadata)}]: {text.strip()}"
