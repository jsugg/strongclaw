"""Unit tests for common helpers."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.common import deep_merge, load_json, load_overlay


def test_deep_merge_nested_mapping() -> None:
    base = {"gateway": {"bind": "loopback", "auth": {"mode": "token"}}}
    overlay = {"gateway": {"auth": {"allowTailscale": False}}}
    assert deep_merge(base, overlay) == {
        "gateway": {
            "bind": "loopback",
            "auth": {"mode": "token", "allowTailscale": False},
        }
    }


def test_load_overlay_accepts_full_json5_syntax(tmp_path: pathlib.Path) -> None:
    overlay_path = tmp_path / "overlay.json5"
    overlay_path.write_text(
        """
        {
          memory: {
            backend: 'qmd',
            enabled: true,
          },
        }
        """.strip(),
        encoding="utf-8",
    )

    assert load_overlay(overlay_path) == {
        "memory": {"backend": "qmd", "enabled": True},
    }


def test_load_overlay_rejects_duplicate_keys(tmp_path: pathlib.Path) -> None:
    overlay_path = tmp_path / "overlay.json5"
    overlay_path.write_text("{ memory: 1, memory: 2 }", encoding="utf-8")

    with pytest.raises(ValueError):
        load_overlay(overlay_path)


def test_load_json_remains_strict_for_machine_documents(tmp_path: pathlib.Path) -> None:
    json_path = tmp_path / "payload.json"
    json_path.write_text("{ value: 'json5-only' }", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_json(json_path)
