"""Conversation capture pipeline for StrongClaw hypermemory."""

from __future__ import annotations

import dataclasses
import json
import os
import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal, cast

import requests

from clawops.hypermemory.models import EntryType

CaptureSource = Literal["regex", "llm"]
MessageLike = tuple[int, str, str]
_CAPTURE_KIND_SET = {"fact", "opinion", "entity", "reflection"}


@dataclasses.dataclass(frozen=True, slots=True)
class CaptureCandidate:
    """Candidate durable memory extracted from conversation flow."""

    kind: EntryType
    text: str
    entity: str | None = None
    confidence: float | None = None
    source: CaptureSource = "regex"
    source_turn: int | None = None
    source_role: str | None = None
    fact_key: str | None = None


_REGEX_PATTERNS: tuple[tuple[re.Pattern[str], EntryType, str | None], ...] = (
    (
        re.compile(r"(?i)\bI (?:always|prefer|like to|usually)\s+(.+)$"),
        "opinion",
        None,
    ),
    (
        re.compile(r"(?i)\b(?:Remember|Note|Important):?\s+(.+)$"),
        "fact",
        None,
    ),
    (
        re.compile(r"(?i)\bMy (name|role|team|timezone) is\s+(.+)$"),
        "entity",
        None,
    ),
    (
        re.compile(r"(?i)\b(?:We decided to|The decision is)\s+(.+)$"),
        "fact",
        None,
    ),
    (
        re.compile(r"(?i)\b(?:Don't|Never|Always)\s+(.+)$"),
        "fact",
        None,
    ),
    (
        re.compile(r"(?i)\b([A-Z][A-Za-z0-9_.-]+)\s+(?:is|are|was|were|has|have)\s+(.+)$"),
        "entity",
        "entity",
    ),
)


def extract_candidates_regex(
    messages: Sequence[MessageLike] | Sequence[str],
) -> list[CaptureCandidate]:
    """Extract conservative regex-based memory candidates."""
    normalized = list(_normalize_messages(messages))
    candidates: list[CaptureCandidate] = []
    seen: set[tuple[EntryType, str]] = set()
    for turn_index, role, text in normalized:
        stripped = text.strip()
        if not stripped:
            continue
        for pattern, kind, entity_mode in _REGEX_PATTERNS:
            match = pattern.search(stripped)
            if not match:
                continue
            entity: str | None = None
            memory_text = stripped
            if entity_mode == "entity":
                entity = match.group(1).strip()
                memory_text = f"{entity} {match.group(2).strip()}"
            elif kind == "entity" and match.lastindex and match.lastindex >= 2:
                entity = match.group(2).strip()
            key = (kind, memory_text.casefold())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                CaptureCandidate(
                    kind=kind,
                    text=memory_text,
                    entity=entity,
                    confidence=0.85 if role == "user" else 0.7,
                    source="regex",
                    source_turn=turn_index,
                    source_role=role,
                    fact_key=_infer_fact_key(memory_text),
                )
            )
            break
    return candidates


def extract_candidates_llm(
    messages: Sequence[MessageLike],
    *,
    endpoint: str,
    model: str,
    api_key: str,
    timeout_ms: int,
    batch_size: int = 6,
    batch_overlap: int = 2,
) -> list[CaptureCandidate]:
    """Extract memory candidates through an OpenAI-compatible chat endpoint."""
    if not endpoint.strip():
        raise ValueError("capture LLM endpoint is required")
    if not model.strip():
        raise ValueError("capture LLM model is required")
    normalized = list(_normalize_messages(messages))
    if not normalized:
        return []
    session = requests.Session()
    all_candidates: list[CaptureCandidate] = []
    errors: list[str] = []
    for batch in _batched_messages(normalized, batch_size=batch_size, batch_overlap=batch_overlap):
        try:
            payload = _call_capture_llm(
                session,
                batch=batch,
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                timeout_ms=timeout_ms,
            )
        except Exception as err:
            errors.append(str(err))
            continue
        for candidate in _parse_llm_candidates(payload, batch=batch):
            all_candidates.append(candidate)
    deduped = _dedupe_candidates(all_candidates)
    if deduped or not errors:
        return deduped
    raise RuntimeError("; ".join(errors))


def resolve_capture_api_key(*, api_key_env: str | None, api_key: str | None = None) -> str:
    """Resolve a capture API key from explicit config or the environment."""
    if api_key:
        return api_key.strip()
    if api_key_env:
        return os.environ.get(api_key_env, "").strip()
    return ""


def _normalize_messages(messages: Sequence[MessageLike] | Sequence[str]) -> Iterable[MessageLike]:
    """Yield normalized `(turn, role, text)` tuples."""
    for index, raw_message in enumerate(messages):
        if isinstance(raw_message, str):
            yield (index, "user", raw_message)
            continue
        turn_index, role, text = raw_message
        yield (int(turn_index), str(role), str(text))


def _batched_messages(
    messages: Sequence[MessageLike],
    *,
    batch_size: int,
    batch_overlap: int,
) -> Iterable[list[MessageLike]]:
    """Yield overlapping batches with bounded linear growth."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    step = max(batch_size - max(batch_overlap, 0), 1)
    for start in range(0, len(messages), step):
        batch = list(messages[start : start + batch_size])
        if batch:
            yield batch
        if start + batch_size >= len(messages):
            break


def _call_capture_llm(
    session: requests.Session,
    *,
    batch: Sequence[MessageLike],
    endpoint: str,
    model: str,
    api_key: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Invoke the capture endpoint for one message batch."""
    prompt = _capture_prompt(batch)
    response = session.post(
        _capture_endpoint(endpoint),
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a memory extraction system. Return only JSON with a "
                        "`candidates` array."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        },
        headers=_capture_headers(api_key=api_key),
        timeout=timeout_ms / 1000.0,
    )
    response.raise_for_status()
    body = response.json()
    raw_content = _extract_response_content(body)
    return cast(dict[str, Any], json.loads(_extract_json_object(raw_content)))


def _capture_prompt(batch: Sequence[MessageLike]) -> str:
    """Build the structured extraction prompt for one conversation segment."""
    rendered_messages = "\n".join(
        f"[turn={turn_index} role={role}] {text}" for turn_index, role, text in batch
    )
    return (
        "Extract durable facts from this conversation segment.\n\n"
        'Return JSON: {"candidates":[{"kind":"fact|opinion|entity|reflection",'
        '"text":"...", "entity":null, "confidence":0.0, "fact_key":null, '
        '"source_turn":0, "source_role":"user"}]}\n\n'
        "Rules:\n"
        "- Only extract durable facts or preferences worth remembering across sessions\n"
        "- Each candidate must be atomic\n"
        "- Skip greetings, tool output, diagnostics, and session-local chatter\n"
        "- Use canonical fact keys such as user:name, user:timezone, pref:editor, decision:db when clear\n"
        "- Maximum 5 candidates\n\n"
        f"Conversation:\n{rendered_messages}"
    )


def _capture_endpoint(endpoint: str) -> str:
    """Resolve the HTTP endpoint for chat-completions capture."""
    stripped = endpoint.rstrip("/")
    if stripped.endswith("/chat/completions"):
        return stripped
    return f"{stripped}/chat/completions"


def _capture_headers(*, api_key: str) -> dict[str, str]:
    """Return capture request headers."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_response_content(body: dict[str, Any]) -> str:
    """Extract response text from an OpenAI-compatible JSON body."""
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                chunks = [
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict) and item.get("type") in {None, "text"}
                ]
                if chunks:
                    return "".join(chunks)
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text
    raise ValueError("capture response did not contain assistant content")


def _extract_json_object(raw_content: str) -> str:
    """Extract the first JSON object from a string response."""
    stripped = raw_content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        newline = stripped.find("\n")
        if newline != -1:
            stripped = stripped[newline + 1 :]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("capture response did not contain a JSON object")
    return stripped[start : end + 1]


def _parse_llm_candidates(
    payload: dict[str, Any],
    *,
    batch: Sequence[MessageLike],
) -> list[CaptureCandidate]:
    """Normalize LLM payload candidates into typed dataclasses."""
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        return []
    turns_by_id = {turn_index: (role, text) for turn_index, role, text in batch}
    fallback_turn = batch[-1]
    candidates: list[CaptureCandidate] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            continue
        kind = str(raw_candidate.get("kind", "")).strip().lower()
        text = str(raw_candidate.get("text", "")).strip()
        if kind not in _CAPTURE_KIND_SET or not text:
            continue
        confidence = raw_candidate.get("confidence")
        normalized_confidence = None
        if isinstance(confidence, (int, float)):
            normalized_confidence = max(0.0, min(float(confidence), 1.0))
        source_turn = raw_candidate.get("source_turn", raw_candidate.get("sourceTurn"))
        if not isinstance(source_turn, int) or source_turn not in turns_by_id:
            source_turn = fallback_turn[0]
        source_role = raw_candidate.get("source_role", raw_candidate.get("sourceRole"))
        if not isinstance(source_role, str) or not source_role.strip():
            source_role = turns_by_id.get(source_turn, (fallback_turn[1],))[0]
        candidates.append(
            CaptureCandidate(
                kind=cast(EntryType, kind),
                text=text,
                entity=_optional_string(raw_candidate.get("entity")),
                confidence=normalized_confidence,
                source="llm",
                source_turn=source_turn,
                source_role=source_role,
                fact_key=_optional_string(
                    raw_candidate.get("fact_key", raw_candidate.get("factKey"))
                )
                or _infer_fact_key(text),
            )
        )
    return candidates


def _dedupe_candidates(candidates: Sequence[CaptureCandidate]) -> list[CaptureCandidate]:
    """Deduplicate capture candidates by kind and normalized text."""
    deduped: list[CaptureCandidate] = []
    seen: set[tuple[EntryType, str]] = set()
    for candidate in candidates:
        key = (candidate.kind, candidate.text.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _optional_string(value: object) -> str | None:
    """Normalize a maybe-string value."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _infer_fact_key(text: str) -> str | None:
    """Infer a canonical fact key from capture text when obvious."""
    normalized = text.strip()
    patterns: tuple[tuple[re.Pattern[str], str | None], ...] = (
        (re.compile(r"(?i)\bmy name is\b"), "user:name"),
        (re.compile(r"(?i)\bmy timezone is\b"), "user:timezone"),
        (re.compile(r"(?i)\bmy role is\b"), "user:role"),
        (re.compile(r"(?i)\bmy team is\b"), "user:team"),
        (re.compile(r"(?i)\bI (?:use|prefer)\s+.+\s+(?:as\s+)?(?:my\s+)?editor\b"), "pref:editor"),
        (re.compile(r"(?i)\bI (?:use|prefer)\s+.+\s+(?:theme|mode)\b"), "pref:theme"),
        (
            re.compile(
                r"(?i)\bI (?:prefer|use)\s+.+\s+(?:for|as)\s+(?:my\s+)?(?:primary\s+)?(?:programming\s+)?language\b"
            ),
            "pref:language",
        ),
        (
            re.compile(
                r"(?i)\b(?:we|the team) (?:decided|chose|agreed) to use .+ (?:for|as) (.+)$"
            ),
            None,
        ),
    )
    for pattern, fixed_key in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        if fixed_key is not None:
            return fixed_key
        subject = re.sub(r"[^a-z0-9]+", "-", match.group(1).strip().lower()).strip("-")
        if subject:
            return f"decision:{subject}"
    return None
