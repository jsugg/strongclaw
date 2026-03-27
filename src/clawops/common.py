"""Shared helpers for the clawops companion tooling."""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
import pathlib
import time
from typing import Any, Mapping, cast

import json5
import yaml


def _empty_extra() -> dict[str, Any]:
    """Return an empty extra payload with a concrete type."""
    return {}


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
    """Load strict JSON content from *path*."""
    return json.loads(load_text(path))


def load_json5(path: pathlib.Path, *, allow_duplicate_keys: bool = False) -> Any:
    """Load full JSON5 content from *path* for human-edited overlays."""
    return cast(Any, json5.loads(load_text(path), allow_duplicate_keys=allow_duplicate_keys))


def load_overlay(path: pathlib.Path) -> Any:
    """Load human-edited overlay content while rejecting duplicate keys."""
    return load_json5(path, allow_duplicate_keys=False)


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
        merged: dict[str, Any] = {
            str(key): value for key, value in cast(Mapping[object, Any], base).items()
        }
        for key, value in cast(Mapping[object, Any], overlay).items():
            key_text = str(key)
            if key_text in merged:
                merged[key_text] = deep_merge(merged[key_text], value)
            else:
                merged[key_text] = value
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
            if not match_mapping(
                cast(Mapping[str, Any], expected),
                cast(Mapping[str, Any], actual),
            ):
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
    extra: dict[str, Any] = dataclasses.field(default_factory=_empty_extra)

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a serializable dictionary."""
        return {"ok": self.ok, "message": self.message, **self.extra}
