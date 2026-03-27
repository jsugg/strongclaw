"""Pure helpers for canonical hypermemory storage behavior."""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, cast

from clawops.common import ensure_parent
from clawops.hypermemory.capture import CaptureCandidate
from clawops.hypermemory.config import HypermemoryConfig, resolve_under_workspace
from clawops.hypermemory.defaults import (
    BANK_HEADERS,
    FACT_KEY_INFERENCE_RULES,
    FACT_QUERY_RULES,
    MEMORY_PRO_IMPORTANCE_MAP,
    WRITABLE_PREFIXES,
)
from clawops.hypermemory.governance import should_auto_apply, validate_scope
from clawops.hypermemory.models import (
    EvidenceEntry,
    FactCategory,
    ProposalRecord,
    ReflectionMode,
    SearchHit,
    Tier,
)
from clawops.hypermemory.noise import is_noise
from clawops.hypermemory.parser import parse_typed_entry
from clawops.hypermemory.utils import sha256, slugify


def is_noise_entry(text: str, *, config: HypermemoryConfig) -> bool:
    """Return whether *text* should be rejected as durable noise."""
    return config.noise.enabled and is_noise(text, config=config.noise)


def passes_admission(candidate: CaptureCandidate, *, config: HypermemoryConfig) -> bool:
    """Return whether a capture candidate clears optional admission control."""
    if not config.admission.enabled:
        return True
    prior = float(config.admission.type_priors.get(candidate.kind, 0.0))
    if prior < config.admission.min_confidence:
        return False
    if candidate.confidence is None:
        return True
    return candidate.confidence >= config.admission.min_confidence


def normalize_tier(value: str | Tier | None) -> Tier:
    """Return a validated lifecycle tier."""
    if isinstance(value, str) and value.strip().lower() in {"core", "working", "peripheral"}:
        return cast(Tier, value.strip().lower())
    return "working"


def infer_fact_key(
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    text: str,
) -> str | None:
    """Infer a canonical fact key from one durable entry."""
    del kind
    normalized = text.strip()
    for pattern, fixed_key in FACT_KEY_INFERENCE_RULES:
        match = pattern.search(normalized)
        if not match:
            continue
        if fixed_key is not None:
            return fixed_key
        subject = re.sub(r"[^a-z0-9]+", "-", match.group(1).strip().lower()).strip("-")
        if subject:
            return f"decision:{subject}"
    return None


def infer_query_fact_key(query: str) -> str | None:
    """Infer a fact key from a user query."""
    stripped = query.strip()
    if re.fullmatch(r"[a-z]+:[a-z0-9_-]+", stripped):
        return stripped
    for pattern, fact_key in FACT_QUERY_RULES:
        if pattern.search(stripped):
            return fact_key
    return None


def fact_category(fact_key: str) -> FactCategory:
    """Map a fact key to its registry category."""
    if fact_key.startswith("user:"):
        return "profile"
    if fact_key.startswith("pref:"):
        return "preference"
    if fact_key.startswith("decision:"):
        return "decision"
    return "entity"


def entry_hash_prefix(entry_line: str) -> str:
    """Return a short, stable reference for a canonical entry line."""
    return sha256(entry_line.strip())[:8]


def typed_entry_text(entry_line: str) -> str:
    """Extract the human-authored body from one typed entry line."""
    stripped = entry_line.strip()
    if stripped.startswith(("- ", "* ")):
        stripped = stripped[2:].strip()
    if ": " in stripped:
        return stripped.split(": ", 1)[1].strip()
    return stripped


def search_hit_text(hit: SearchHit) -> str:
    """Extract the entry body text from a search hit snippet."""
    return typed_entry_text(hit.snippet)


def load_entities_json(raw_value: object) -> list[str]:
    """Decode a JSON list of entity names."""
    if not isinstance(raw_value, str):
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in cast(list[object], payload) if isinstance(item, str)]


def load_evidence_json(raw_value: object) -> list[dict[str, object]]:
    """Decode persisted evidence JSON into normalized provenance entries."""
    if not isinstance(raw_value, str):
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    evidence_entries: list[dict[str, object]] = []
    for raw_entry in cast(list[object], payload):
        if not isinstance(raw_entry, dict):
            continue
        try:
            entry_mapping = cast(dict[str, object], raw_entry)
            evidence_entries.append(EvidenceEntry.from_dict(entry_mapping).to_dict())
        except ValueError:
            continue
    return evidence_entries


def memory_pro_importance(*, item_type: str, confidence: float | None) -> float:
    """Return a conservative import importance for the new memory backend."""
    if item_type == "opinion" and confidence is not None:
        return max(0.0, min(1.0, confidence))
    return MEMORY_PRO_IMPORTANCE_MAP[item_type]


def memory_pro_timestamp_ms(value: str) -> int:
    """Convert a stored ISO timestamp into Unix milliseconds."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.now(tz=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def resolve_read_path(config: HypermemoryConfig, rel_path: str) -> pathlib.Path:
    """Resolve a safe readable path within the workspace."""
    path = (config.workspace_root / rel_path).resolve()
    resolve_under_workspace(config.workspace_root, path)
    if not path.exists():
        return path
    if path.is_dir():
        raise IsADirectoryError(path)
    return path


def resolve_writable_path(config: HypermemoryConfig, rel_path: str) -> pathlib.Path:
    """Resolve a safe writable path within the workspace."""
    normalized = rel_path.strip()
    if not normalized.startswith(WRITABLE_PREFIXES):
        raise PermissionError(f"{rel_path} is not writable")
    path = (config.workspace_root / normalized).resolve()
    resolve_under_workspace(config.workspace_root, path)
    return path


def store_target(
    config: HypermemoryConfig,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    entity: str | None = None,
) -> pathlib.Path:
    """Return the canonical target path for a durable entry."""
    bank_dir = config.workspace_root / config.bank_dir
    if kind == "fact":
        return bank_dir / "world.md"
    if kind == "reflection":
        return bank_dir / "experience.md"
    if kind == "opinion":
        return bank_dir / "opinions.md"
    name = slugify(entity or "general")
    return bank_dir / "entities" / f"{name}.md"


def format_entry_line(
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    text: str,
    scope: str,
    entity: str | None = None,
    confidence: float | None = None,
    fact_key: str | None = None,
    importance: float | None = None,
    tier: Tier = "working",
    access_count: int = 0,
    last_access_date: str | None = None,
    injected_count: int = 0,
    confirmed_count: int = 0,
    bad_recall_count: int = 0,
    invalidated_at: str | None = None,
    supersedes: str | None = None,
    evidence: Sequence[str] = (),
    contradicts: Sequence[str] = (),
) -> str:
    """Format a canonical typed entry line."""
    label = kind.capitalize()
    metadata: list[str] = []
    if kind == "entity":
        metadata.append((entity or text).strip())
    if kind == "opinion" and confidence is not None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        metadata.append(f"c={confidence:.2f}")
    metadata.append(f"scope={scope}")
    if importance is not None:
        metadata.append(f"importance={max(0.0, min(importance, 1.0)):.2f}")
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
        metadata.append(
            f"evidence={'|'.join(entry.strip() for entry in evidence if entry.strip())}"
        )
    if contradicts:
        metadata.append(
            f"contradicts={'|'.join(entry.strip() for entry in contradicts if entry.strip())}"
        )
    return f"{label}[{','.join(metadata)}]: {text.strip()}"


def document_header(path: pathlib.Path, *, kind: str) -> str:
    """Return the default header for a writable canonical file."""
    if kind == "proposal":
        return "# Memory Proposals\n\n## Entries\n"
    if path.name in {"world.md", "experience.md", "opinions.md"}:
        mapped_kind = path.stem.rstrip("s") if path.stem != "experience" else "reflection"
        return BANK_HEADERS.get(mapped_kind, "# Entries\n\n")
    if path.parent.name == "entities":
        title = path.stem.replace("-", " ").title()
        return f"# Entity: {title}\n\n## Entries\n"
    return "# Entries\n\n"


def entry_identity(
    entry_line: str, *, config: HypermemoryConfig
) -> tuple[str, str, str | None] | None:
    """Return a semantic identity for a canonical entry line."""
    parsed = parse_typed_entry(entry_line.strip(), default_scope=config.governance.default_scope)
    if parsed is None:
        return None
    if parsed.fact_key:
        return (parsed.item_type, f"fact_key:{parsed.fact_key}", None)
    text = parsed.entry_line
    if ": " in text:
        _, normalized_body = text.split(": ", 1)
    else:
        normalized_body = text
    entity_name = next(iter(parsed.entities), None)
    return (parsed.item_type, normalized_body.lower(), entity_name)


def append_unique_entry(
    config: HypermemoryConfig,
    path: pathlib.Path,
    *,
    kind: str,
    entry_line: str,
) -> bool:
    """Append *entry_line* if it is not already present semantically."""
    ensure_parent(path)
    current = (
        path.read_text(encoding="utf-8") if path.exists() else document_header(path, kind=kind)
    )
    lines = current.splitlines()
    target_identity = entry_identity(entry_line, config=config)
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped == entry_line.strip():
            return False
        if not stripped.startswith(("- ", "* ")):
            continue
        existing_entry = stripped[2:].strip()
        parsed = parse_typed_entry(
            existing_entry,
            default_scope=config.governance.default_scope,
        )
        if parsed is not None and parsed.invalidated_at is not None:
            continue
        existing_identity = entry_identity(existing_entry, config=config)
        if target_identity and existing_identity == target_identity:
            return False
    if current and not current.endswith("\n"):
        current += "\n"
    current += f"- {entry_line}\n"
    path.write_text(current, encoding="utf-8")
    return True


def build_proposal(
    config: HypermemoryConfig,
    *,
    kind: Literal["fact", "reflection", "opinion", "entity"],
    entry_line: str,
    scope: str,
    source_rel_path: str,
    source_line: int,
    entity: str | None,
    confidence: float | None,
    mode: ReflectionMode,
) -> ProposalRecord:
    """Build a stable proposal record."""
    normalized_scope = validate_scope(scope)
    proposal_id = sha256(f"{source_rel_path}:{source_line}:{entry_line}")
    scope_auto_apply = should_auto_apply(normalized_scope, config.governance)
    auto_apply = mode == "safe" and scope_auto_apply
    if mode == "apply":
        auto_apply = scope_auto_apply
    status: Literal["pending", "applied"] = "applied" if auto_apply else "pending"
    return ProposalRecord(
        proposal_id=proposal_id,
        kind=kind,
        entry_line=entry_line,
        scope=normalized_scope,
        source_rel_path=source_rel_path,
        source_line=source_line,
        status=status,
        entity=entity,
        confidence=confidence,
    )


def format_proposal_line(proposal: ProposalRecord) -> str:
    """Format a canonical proposal log entry."""
    metadata = [
        f"id={proposal.proposal_id}",
        f"status={proposal.status}",
        f"kind={proposal.kind}",
        f"scope={proposal.scope}",
        f"source={proposal.source_rel_path}#L{proposal.source_line}",
    ]
    if proposal.entity:
        metadata.append(f"entity={proposal.entity}")
    if proposal.confidence is not None:
        metadata.append(f"c={proposal.confidence:.2f}")
    return f"Proposal[{','.join(metadata)}]: {proposal.entry_line}"


def proposal_kind(
    entry_line: str,
    *,
    config: HypermemoryConfig,
) -> Literal["fact", "reflection", "opinion", "entity"]:
    """Extract the proposal target kind from a proposal line."""
    parsed = parse_typed_entry(entry_line, default_scope=config.governance.default_scope)
    if parsed is None:
        return "fact"
    text = parsed.entry_line
    target = parse_typed_entry(
        text.split(": ", 1)[1],
        default_scope=config.governance.default_scope,
    )
    if target is None:
        return "fact"
    if target.item_type in {"fact", "reflection", "opinion", "entity"}:
        return cast(Literal["fact", "reflection", "opinion", "entity"], target.item_type)
    return "fact"


def allows_memory_pro_export_path(
    config: HypermemoryConfig,
    *,
    rel_path: str,
    include_daily: bool,
) -> bool:
    """Return whether *rel_path* is safe to export into the new backend."""
    if rel_path in config.memory_file_names:
        return True
    bank_prefix = f"{config.bank_dir}/"
    if rel_path.startswith(bank_prefix):
        return True
    if include_daily:
        daily_prefix = f"{config.daily_dir}/"
        return rel_path.startswith(daily_prefix)
    return False
