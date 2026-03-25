"""Regression tests for fresh-host Docker image helpers."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any


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
    sys.modules[spec.name] = module
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


def test_ensure_images_loads_cache_then_pulls_only_missing(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    aux_compose = tmp_path / "aux.yaml"
    aux_compose.write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")
    browser_compose = tmp_path / "browser.yaml"
    browser_compose.write_text(
        "services:\n  browserlab:\n    image: mcr.microsoft.com/playwright:v1.41.1-jammy\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "all-images.tar").write_text("placeholder\n", encoding="utf-8")

    list_calls = iter(
        [
            ["postgres:16"],
            ["postgres:16"],
            ["postgres:16", "mcr.microsoft.com/playwright:v1.41.1-jammy"],
        ]
    )
    pulled: list[list[str]] = []
    saved: list[list[str]] = []
    state: dict[str, Any] = {"cache_loaded": False}

    module.list_local_images = lambda images: next(list_calls)

    def fake_load_image_cache(
        cache_path: pathlib.Path, images: list[str]
    ) -> tuple[list[str], list[str]]:
        assert cache_path == cache_dir
        assert images == ["mcr.microsoft.com/playwright:v1.41.1-jammy"]
        state["cache_loaded"] = True
        return ["mcr.microsoft.com/playwright:v1.41.1-jammy"], []

    def fake_pull_images(images: list[str], *, parallelism: int) -> int:
        pulled.append(list(images))
        assert parallelism == 2
        return 0

    def fake_save_image_cache(
        cache_path: pathlib.Path, images: list[str]
    ) -> tuple[list[str], list[str]]:
        saved.append(list(images))
        assert cache_path == cache_dir
        return list(images), []

    module.load_image_cache = fake_load_image_cache
    module.pull_images = fake_pull_images
    module.save_image_cache = fake_save_image_cache

    rc = module.ensure_images(
        [aux_compose, browser_compose],
        parallelism=2,
        cache_dir=cache_dir,
        report_path=tmp_path / "report.json",
    )

    assert rc == 0
    assert state["cache_loaded"] is True
    assert pulled == [["mcr.microsoft.com/playwright:v1.41.1-jammy"]]
    assert saved == [["postgres:16", "mcr.microsoft.com/playwright:v1.41.1-jammy"]]


def test_ensure_images_skips_cache_when_not_requested(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "aux.yaml"
    compose_path.write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")

    list_calls = iter([[], [], ["postgres:16"]])
    pulled: list[list[str]] = []
    state: dict[str, bool] = {"cache_loaded": False}

    module.list_local_images = lambda images: next(list_calls)
    module.load_image_cache = lambda cache_path, images: (
        state.__setitem__("cache_loaded", True) or [],
        [],
    )
    module.pull_images = lambda images, *, parallelism: pulled.append(list(images)) or 0
    module.save_image_cache = lambda cache_path, images: (list(images), [])

    rc = module.ensure_images([compose_path], parallelism=1, cache_dir=None, report_path=None)

    assert rc == 0
    assert state["cache_loaded"] is False
    assert pulled == [["postgres:16"]]


def test_ensure_images_writes_failure_report_on_pull_error(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "aux.yaml"
    compose_path.write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    list_calls = iter([[], [], []])
    module.list_local_images = lambda images: next(list_calls)
    module.pull_images = lambda images, *, parallelism: 1
    module.load_image_cache = lambda cache_path, images: ([], [])

    rc = module.ensure_images(
        [compose_path],
        parallelism=3,
        cache_dir=None,
        report_path=report_path,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert rc == 1
    assert report["failure_reason"] == "docker pull failed"
    assert report["missing_after_pull"] == ["postgres:16"]
