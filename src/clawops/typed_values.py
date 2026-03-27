"""Runtime coercion helpers for dynamic mapping and sequence inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

type ObjectMapping = Mapping[str, object]
type MutableObjectMapping = dict[str, object]


def empty_object_dict() -> MutableObjectMapping:
    """Return a typed empty dictionary for object payloads."""
    return {}


def as_mapping(value: object, *, path: str) -> ObjectMapping:
    """Validate and return a mapping with string keys and object values."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return cast(ObjectMapping, value)


def as_optional_mapping(value: object, *, path: str) -> ObjectMapping | None:
    """Validate and return an optional mapping."""
    if value is None:
        return None
    return as_mapping(value, path=path)


def as_string(value: object, *, path: str) -> str:
    """Validate and return a string."""
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    return value


def as_optional_string(value: object, *, path: str) -> str | None:
    """Validate and return an optional string."""
    if value is None:
        return None
    return as_string(value, path=path)


def as_bool(value: object, *, path: str) -> bool:
    """Validate and return a boolean."""
    if not isinstance(value, bool):
        raise TypeError(f"{path} must be a boolean")
    return value


def as_int(value: object, *, path: str) -> int:
    """Validate and return an integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{path} must be an integer")
    return value


def as_string_list(value: object, *, path: str) -> tuple[str, ...]:
    """Validate and return a tuple of strings."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{path} must be a sequence of strings")
    sequence = cast(Sequence[object], value)
    return tuple(as_string(item, path=f"{path}[{index}]") for index, item in enumerate(sequence))


def as_mapping_list(value: object, *, path: str) -> tuple[ObjectMapping, ...]:
    """Validate and return a tuple of mappings."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{path} must be a sequence of mappings")
    sequence = cast(Sequence[object], value)
    return tuple(as_mapping(item, path=f"{path}[{index}]") for index, item in enumerate(sequence))
