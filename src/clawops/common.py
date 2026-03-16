"""Shared helpers for the clawops companion tooling."""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import time
from typing import Any, Mapping

import yaml


def ensure_parent(path: pathlib.Path) -> None:
    """Create the parent directory for *path* if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)


def load_text(path: pathlib.Path) -> str:
    """Load a UTF-8 text file."""
    return path.read_text(encoding="utf-8")


def write_text(path: pathlib.Path, content: str) -> None:
    """Write a UTF-8 text file."""
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def load_json(path: pathlib.Path) -> Any:
    """Load JSON or JSON5-compatible content from *path*."""
    text = load_text(path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_strip_json5_comments_and_trailing_commas(text))


def _strip_json5_comments_and_trailing_commas(text: str) -> str:
    """Normalize a JSON5-lite document into strict JSON.

    The repository only relies on comment and trailing-comma support for
    operator-edited `.json5` overlays, so the normalizer intentionally keeps a
    narrow compatibility surface instead of implementing the full JSON5 grammar.
    """

    def _strip_comments(value: str) -> str:
        result: list[str] = []
        in_string = False
        string_quote = ""
        escape = False
        in_line_comment = False
        in_block_comment = False
        index = 0
        while index < len(value):
            char = value[index]
            next_char = value[index + 1] if index + 1 < len(value) else ""
            if in_line_comment:
                if char == "\n":
                    in_line_comment = False
                    result.append(char)
                index += 1
                continue
            if in_block_comment:
                if char == "*" and next_char == "/":
                    in_block_comment = False
                    index += 2
                    continue
                index += 1
                continue
            if in_string:
                result.append(char)
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == string_quote:
                    in_string = False
                index += 1
                continue
            if char in {'"', "'"}:
                in_string = True
                string_quote = char
                result.append(char)
                index += 1
                continue
            if char == "/" and next_char == "/":
                in_line_comment = True
                index += 2
                continue
            if char == "/" and next_char == "*":
                in_block_comment = True
                index += 2
                continue
            result.append(char)
            index += 1
        return "".join(result)

    def _strip_trailing_commas(value: str) -> str:
        result: list[str] = []
        in_string = False
        string_quote = ""
        escape = False
        index = 0
        while index < len(value):
            char = value[index]
            if in_string:
                result.append(char)
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == string_quote:
                    in_string = False
                index += 1
                continue
            if char in {'"', "'"}:
                in_string = True
                string_quote = char
                result.append(char)
                index += 1
                continue
            if char == ",":
                match = re.match(r"\s*([}\]])", value[index + 1 :], flags=re.DOTALL)
                if match is not None:
                    index += 1
                    continue
            result.append(char)
            index += 1
        return "".join(result)

    without_comments = _strip_comments(text)
    return _strip_trailing_commas(without_comments)


def dump_json(value: Any, *, indent: int = 2) -> str:
    """Serialize *value* as stable JSON."""
    return json.dumps(value, indent=indent, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(path: pathlib.Path, value: Any, *, indent: int = 2) -> None:
    """Write JSON to *path*."""
    write_text(path, dump_json(value, indent=indent))


def load_yaml(path: pathlib.Path) -> Any:
    """Load YAML from *path*."""
    return yaml.safe_load(load_text(path))


def dump_yaml(value: Any) -> str:
    """Serialize YAML in a stable human-readable format."""
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def write_yaml(path: pathlib.Path, value: Any) -> None:
    """Write YAML to *path*."""
    write_text(path, dump_yaml(value))


def sha256_hex(data: bytes | str) -> str:
    """Return a hex SHA-256 digest."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    """Return a compact canonical JSON string for hashing and journaling."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def utc_now_ms() -> int:
    """Return the current UTC epoch in milliseconds."""
    return int(time.time() * 1000)


def expand(path: str | os.PathLike[str]) -> pathlib.Path:
    """Expand a filesystem path."""
    return pathlib.Path(path).expanduser().resolve()


def deep_merge(base: Any, overlay: Any) -> Any:
    """Recursively merge *overlay* onto *base*.

    Mapping values are merged recursively. Other values replace the base.
    """
    if isinstance(base, Mapping) and isinstance(overlay, Mapping):
        merged: dict[str, Any] = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return overlay


def match_mapping(rule: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    """Return True when *payload* satisfies *rule*.

    Nested mappings are matched recursively. String values support shell-style
    wildcards.
    """
    for key, expected in rule.items():
        if key not in payload:
            return False
        actual = payload[key]
        if isinstance(expected, Mapping):
            if not isinstance(actual, Mapping):
                return False
            if not match_mapping(expected, actual):
                return False
            continue
        if isinstance(expected, list):
            if actual not in expected:
                return False
            continue
        if isinstance(expected, str) and any(ch in expected for ch in "*?[]"):
            if not fnmatch.fnmatch(str(actual), expected):
                return False
            continue
        if actual != expected:
            return False
    return True


@dataclasses.dataclass(slots=True)
class ResultSummary:
    """Common result envelope used by several CLIs."""

    ok: bool
    message: str
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a serializable dictionary."""
        return {"ok": self.ok, "message": self.message, **self.extra}
