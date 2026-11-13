"""Deterministic OpenAI- and Ollama-compatible model server for feature verification.

The server stands in for every model provider the product talks to on a runner: the OpenClaw
agent chain (an explicit OpenAI-compatible ``models.providers`` entry), the memory plugins'
embedding and extraction endpoints, and the LiteLLM sidecar's Ollama upstream. Replies are
canned, so assertions can rely on exact content, and every request is journaled so a test can
prove the product actually reached the model rather than short-circuiting.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, cast

CHAT_MODEL_ID: Final[str] = "featver-chat"
EMBEDDING_MODEL_ID: Final[str] = "featver-embed"
DEFAULT_EMBEDDING_DIMENSIONS: Final[int] = 768
DEFAULT_CONTEXT_WINDOW: Final[int] = 131_072
REPLY_PREFIX: Final[str] = "FEATVER_REPLY"
ECHO_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bFV-[A-Za-z0-9][A-Za-z0-9_-]{3,63}\b")
JOURNAL_PATH: Final[str] = "/__featver/requests"
HEALTH_PATH: Final[str] = "/__featver/health"
_MAX_JOURNAL_ENTRIES: Final[int] = 2000
_PROMPT_EXCERPT_CHARS: Final[int] = 4000
# Models advertised by /v1/models and /api/tags; requests naming any other model still succeed.
ADVERTISED_MODELS: Final[tuple[str, ...]] = (
    CHAT_MODEL_ID,
    EMBEDDING_MODEL_ID,
    "nomic-embed-text",
    "qwen2.5:7b",
)

type JsonObject = dict[str, object]
type ReplyRule = tuple[Callable[[str], bool], Callable[[str], str]]


def _as_object(value: object) -> JsonObject:
    """Return *value* as a string-keyed mapping, or an empty mapping."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in cast(Mapping[object, object], value).items()}


def _content_text(content: object) -> str:
    """Flatten OpenAI/Ollama message content (string or content parts) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in cast(Sequence[object], content):
            part_mapping = _as_object(part)
            text = part_mapping.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _messages_text(payload: JsonObject) -> tuple[str, str]:
    """Return (all message text, latest user message text) for a chat payload."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        prompt = payload.get("prompt")
        text = prompt if isinstance(prompt, str) else ""
        return text, text
    all_parts: list[str] = []
    latest_user = ""
    for raw_message in cast(Sequence[object], messages):
        message = _as_object(raw_message)
        text = _content_text(message.get("content"))
        all_parts.append(text)
        if message.get("role") == "user":
            latest_user = text
    return "\n".join(all_parts), latest_user


SYSTEM_ROLES: Final[frozenset[str]] = frozenset({"system", "developer"})


def system_prompt_tokens(payload: JsonObject) -> list[str]:
    """Return the FV- tokens in a chat payload's system/developer messages, in order, unique.

    The journal keeps only bounded prompt excerpts, and the system prompt alone outgrows them,
    so plugin-injected system context is recorded by the tokens it carries.
    """
    messages = payload.get("messages")
    texts: list[str] = []
    if isinstance(messages, list):
        for raw_message in cast(Sequence[object], messages):
            message = _as_object(raw_message)
            if message.get("role") in SYSTEM_ROLES:
                texts.append(_content_text(message.get("content")))
    system = payload.get("system")
    if isinstance(system, str):
        texts.append(system)
    return list(dict.fromkeys(ECHO_TOKEN_PATTERN.findall("\n".join(texts))))


def _is_reflection_prompt(text: str) -> bool:
    """Return whether *text* is the memory-lancedb-pro reflection prompt."""
    return "## Invariants" in text and "## Derived" in text


def _reflection_reply(_: str) -> str:
    """Return a reflection document the lancedb-pro parser accepts."""
    return (
        "## Context (session background)\n"
        "- Feature verification session for the StrongClaw memory plugin.\n\n"
        "## Decisions (durable)\n"
        "- Keep feature verification replies deterministic.\n\n"
        "## User model deltas (about the human)\n"
        "- (none captured)\n\n"
        "## Agent model deltas (about the assistant/system)\n"
        "- (none captured)\n\n"
        "## Lessons & pitfalls (symptom / cause / fix / prevention)\n"
        "- (none captured)\n\n"
        "## Learning governance candidates (.learnings / promotion / skill extraction)\n"
        "- (none captured)\n\n"
        "## Open loops / next actions\n"
        "- (none captured)\n\n"
        "## Retrieval tags / keywords\n"
        "- featver, reflection\n\n"
        "## Invariants\n"
        "- Always verify features against the real runtime (FEATVER_REFLECTION_INVARIANT).\n\n"
        "## Derived\n"
        "- Prefer the embedded runner for reflection runs (FEATVER_REFLECTION_DERIVED).\n"
    )


def _is_extraction_prompt(text: str) -> bool:
    """Return whether *text* is the memory-lancedb-pro smart-extraction prompt."""
    return "extract memories worth long-term preservation" in text


def _extraction_reply(_: str) -> str:
    """Return an empty structured-extraction result."""
    return json.dumps({"memories": []})


def _default_reply(latest_user: str) -> str:
    """Return the canned agent reply, echoing any FV- token from the latest user turn."""
    tokens = ECHO_TOKEN_PATTERN.findall(latest_user)
    suffix = " ".join(dict.fromkeys(tokens)) if tokens else "acknowledged"
    return f"{REPLY_PREFIX} {suffix}"


REPLY_RULES: Final[tuple[ReplyRule, ...]] = (
    (_is_reflection_prompt, _reflection_reply),
    (_is_extraction_prompt, _extraction_reply),
)


def canned_reply(all_text: str, latest_user: str) -> str:
    """Return the deterministic reply for one chat request."""
    for matches, respond in REPLY_RULES:
        if matches(all_text):
            return respond(all_text)
    return _default_reply(latest_user)


def deterministic_embedding(
    text: str, dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS
) -> list[float]:
    """Return a unit-length, content-derived embedding so equal texts embed identically."""
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        values.extend((byte - 127.5) / 127.5 for byte in digest)
        counter += 1
    vector = values[:dimensions]
    norm = math.sqrt(sum(component * component for component in vector)) or 1.0
    return [round(component / norm, 6) for component in vector]


@dataclass(slots=True)
class RequestJournal:
    """Thread-safe bounded record of the requests the server answered."""

    entries: list[JsonObject] = field(default_factory=lambda: cast(list[JsonObject], []))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, entry: JsonObject) -> None:
        """Append one entry, dropping the oldest beyond the bound."""
        with self.lock:
            self.entries.append(entry)
            del self.entries[:-_MAX_JOURNAL_ENTRIES]

    def snapshot(self) -> list[JsonObject]:
        """Return a copy of the recorded entries."""
        with self.lock:
            return list(self.entries)


class FakeModelHandler(BaseHTTPRequestHandler):
    """Route OpenAI-compatible and Ollama-native requests to canned responders."""

    server_version = "featver-fake-model/1"
    journal: RequestJournal = RequestJournal()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence per-request stderr logging; the journal is the audit trail."""
        del format, args

    def _read_json(self) -> JsonObject:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return _as_object(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, payload: JsonObject, *, reply: str | None = None) -> None:
        all_text, latest_user = _messages_text(payload)
        self.journal.record(
            {
                "at": round(time.time(), 3),
                "method": self.command,
                "path": self.path.split("?", 1)[0],
                "port": cast(tuple[str, int], self.server.server_address)[1],
                "model": payload.get("model"),
                "stream": bool(payload.get("stream")),
                "latestUser": latest_user[:_PROMPT_EXCERPT_CHARS],
                "systemTokens": system_prompt_tokens(payload),
                "prompt": all_text[:_PROMPT_EXCERPT_CHARS],
                "reply": reply,
            }
        )

    def do_GET(self) -> None:  # noqa: N802
        """Serve discovery, health, and journal endpoints."""
        path = self.path.split("?", 1)[0]
        if path == HEALTH_PATH:
            self._send_json({"ok": True})
        elif path == JOURNAL_PATH:
            self._send_json({"requests": self.journal.snapshot()})
        elif path in {"/v1/models", "/models"}:
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {"id": model, "object": "model", "created": 0, "owned_by": "featver"}
                        for model in ADVERTISED_MODELS
                    ],
                }
            )
        elif path == "/api/tags":
            self._send_json(
                {"models": [{"name": model, "model": model} for model in ADVERTISED_MODELS]}
            )
        elif path == "/api/version":
            self._send_json({"version": "0.0.0-featver"})
        elif path in {"/", "/health", "/health/liveliness"}:
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": f"unknown path {path}"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        """Serve chat, completion, and embedding endpoints."""
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        if path in {"/v1/chat/completions", "/chat/completions"}:
            self._openai_chat(payload)
        elif path in {"/v1/completions", "/completions"}:
            self._openai_completion(payload)
        elif path in {"/v1/embeddings", "/embeddings"}:
            self._openai_embeddings(payload)
        elif path == "/api/chat":
            self._ollama_chat(payload)
        elif path in {"/api/embed", "/api/embeddings"}:
            self._ollama_embeddings(payload, legacy=path == "/api/embeddings")
        elif path == "/api/show":
            self._send_json(
                {
                    "details": {"family": "featver"},
                    "model_info": {"featver.context_length": DEFAULT_CONTEXT_WINDOW},
                    "capabilities": ["completion", "embedding"],
                }
            )
        else:
            self._record(payload)
            self._send_json({"error": f"unknown path {path}"}, HTTPStatus.NOT_FOUND)

    def _openai_chat(self, payload: JsonObject) -> None:
        all_text, latest_user = _messages_text(payload)
        reply = canned_reply(all_text, latest_user)
        self._record(payload, reply=reply)
        model = str(payload.get("model") or CHAT_MODEL_ID)
        completion_id = f"chatcmpl-featver-{int(time.time() * 1000)}"
        usage = {"prompt_tokens": 16, "completion_tokens": 8, "total_tokens": 24}
        if not payload.get("stream"):
            self._send_json(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            )
            return
        chunks: list[JsonObject] = [
            {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None},
            {"index": 0, "delta": {"content": reply}, "finish_reason": None},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ]
        events: list[JsonObject] = [
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [choice],
            }
            for choice in chunks
        ]
        events.append(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [],
                "usage": usage,
            }
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True

    def _openai_completion(self, payload: JsonObject) -> None:
        prompt = payload.get("prompt")
        text = prompt if isinstance(prompt, str) else ""
        reply = canned_reply(text, text)
        self._record(payload, reply=reply)
        self._send_json(
            {
                "id": "cmpl-featver",
                "object": "text_completion",
                "created": int(time.time()),
                "model": str(payload.get("model") or CHAT_MODEL_ID),
                "choices": [{"index": 0, "text": reply, "finish_reason": "stop"}],
            }
        )

    @staticmethod
    def _embedding_inputs(raw: object) -> list[str]:
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(item) for item in cast(Sequence[object], raw)]
        return [""]

    @staticmethod
    def _dimensions(payload: JsonObject) -> int:
        raw = payload.get("dimensions")
        return raw if isinstance(raw, int) and raw > 0 else DEFAULT_EMBEDDING_DIMENSIONS

    def _openai_embeddings(self, payload: JsonObject) -> None:
        self._record(payload)
        inputs = self._embedding_inputs(payload.get("input"))
        dimensions = self._dimensions(payload)
        self._send_json(
            {
                "object": "list",
                "model": str(payload.get("model") or EMBEDDING_MODEL_ID),
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": deterministic_embedding(text, dimensions),
                    }
                    for index, text in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            }
        )

    def _ollama_embeddings(self, payload: JsonObject, *, legacy: bool) -> None:
        self._record(payload)
        dimensions = self._dimensions(payload)
        if legacy:
            prompt = payload.get("prompt")
            text = prompt if isinstance(prompt, str) else ""
            self._send_json({"embedding": deterministic_embedding(text, dimensions)})
            return
        inputs = self._embedding_inputs(payload.get("input"))
        # Ollama's /api/embed always reports usage; clients such as LiteLLM rely on it.
        self._send_json(
            {
                "model": str(payload.get("model") or EMBEDDING_MODEL_ID),
                "embeddings": [deterministic_embedding(text, dimensions) for text in inputs],
                "total_duration": 1000,
                "load_duration": 100,
                "prompt_eval_count": sum(len(text.split()) or 1 for text in inputs),
            }
        )

    def _ollama_chat(self, payload: JsonObject) -> None:
        all_text, latest_user = _messages_text(payload)
        reply = canned_reply(all_text, latest_user)
        self._record(payload, reply=reply)
        model = str(payload.get("model") or CHAT_MODEL_ID)
        final: JsonObject = {
            "model": model,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": {"role": "assistant", "content": reply},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 16,
            "eval_count": 8,
        }
        if payload.get("stream") is False:
            self._send_json(final)
            return
        body = json.dumps(final).encode("utf-8") + b"\n"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeModelServer:
    """Serve the canned model API on one or more ports from a shared journal."""

    def __init__(self, *, host: str, ports: Sequence[int]) -> None:
        if not ports:
            raise ValueError("FakeModelServer needs at least one port")
        self._servers = [ThreadingHTTPServer((host, port), FakeModelHandler) for port in ports]
        for server in self._servers:
            server.daemon_threads = True
        self._threads: list[threading.Thread] = []

    @property
    def ports(self) -> list[int]:
        """Return the bound ports (resolves ephemeral port 0 requests)."""
        return [int(server.server_address[1]) for server in self._servers]

    def start(self) -> None:
        """Serve every port on a daemon thread."""
        for server in self._servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        """Shut down every listener."""
        for server in self._servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()
