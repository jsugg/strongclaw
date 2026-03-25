"""Tests for packaged hypermemory defaults."""

from __future__ import annotations

from importlib.resources import files

from clawops.hypermemory.defaults import (
    DEFAULT_MEMORY_FILE_NAMES,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_SNIPPET_CHARS,
    defaults_document,
)
from clawops.hypermemory.models import CaptureConfig, QdrantConfig, RankingConfig


def test_hypermemory_defaults_resource_is_packaged() -> None:
    resource = files("clawops.hypermemory").joinpath("resources/defaults.yaml")
    assert resource.is_file()


def test_hypermemory_model_defaults_are_loaded_from_packaged_defaults() -> None:
    defaults = defaults_document()

    assert tuple(defaults["workspace"]["memory_file_names"]) == DEFAULT_MEMORY_FILE_NAMES
    assert RankingConfig().memory_lane_weight == defaults["ranking"]["memory_lane_weight"]
    assert CaptureConfig().batch_size == defaults["capture"]["batch_size"]
    assert DEFAULT_SNIPPET_CHARS == defaults["limits"]["max_snippet_chars"]
    assert QdrantConfig().collection == DEFAULT_QDRANT_COLLECTION
