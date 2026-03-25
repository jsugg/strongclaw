"""Regression tests for fresh-host Docker image helpers."""

from __future__ import annotations

import importlib.util
import pathlib


def _module_path() -> pathlib.Path:
    return (
        pathlib.Path(__file__).resolve().parents[1] / ".github" / "scripts" / "fresh_host_images.py"
    )


def _load_module():
    module_path = _module_path()
    spec = importlib.util.spec_from_file_location("fresh_host_images", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_images_preserves_first_seen_order(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    aux_compose = tmp_path / "aux.yaml"
    aux_compose.write_text(
        """
services:
  postgres:
    image: postgres:16-alpine@sha256:aaa
  qdrant:
    image: qdrant/qdrant:v1.15.5@sha256:bbb
""".strip() + "\n",
        encoding="utf-8",
    )
    browser_compose = tmp_path / "browser.yaml"
    browser_compose.write_text(
        """
services:
  browserlab-proxy:
    image: ubuntu/squid:latest@sha256:ccc
  browserlab-playwright:
    image: mcr.microsoft.com/playwright:v1.41.1-jammy@sha256:ddd
  browserlab-shadow:
    image: qdrant/qdrant:v1.15.5@sha256:bbb
""".strip() + "\n",
        encoding="utf-8",
    )

    images = module.collect_images([aux_compose, browser_compose])

    assert images == [
        "postgres:16-alpine@sha256:aaa",
        "qdrant/qdrant:v1.15.5@sha256:bbb",
        "ubuntu/squid:latest@sha256:ccc",
        "mcr.microsoft.com/playwright:v1.41.1-jammy@sha256:ddd",
    ]


def test_collect_images_rejects_compose_without_images(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "empty.yaml"
    compose_path.write_text("services:\n  noop: {}\n", encoding="utf-8")

    try:
        module.collect_images([compose_path])
    except ValueError as exc:
        assert "No image references found" in str(exc)
    else:
        raise AssertionError("collect_images should reject compose files without image entries")
