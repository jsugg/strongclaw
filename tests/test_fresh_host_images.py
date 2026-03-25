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


def test_ensure_images_pulls_only_missing_images(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "compose.yaml"
    browser_image = (
        "mcr.microsoft.com/playwright:v1.41.1-jammy@sha256:"
        "0f0547cc1492898e84a6fd0847bcfdde15e483b333a66e88b547c9ce15aea6c7"
    )
    compose_path.write_text(
        f"""
services:
  postgres:
    image: postgres:16
  browserlab:
    image: {browser_image}
""".strip() + "\n",
        encoding="utf-8",
    )
    pulled: list[list[str]] = []
    list_calls = iter([["postgres:16"], ["postgres:16", browser_image]])

    module.list_local_images = lambda images: next(list_calls)
    module.pull_images = lambda images, *, parallelism, max_attempts=3: (
        pulled.append(list(images))
        or module.PullReport(
            exit_code=0,
            pulled_images=list(images),
            failed_images=[],
            attempt_count=1,
            retried_images=[],
        )
    )

    rc = module.ensure_images([compose_path], parallelism=2, report_path=tmp_path / "report.json")

    assert rc == 0
    assert pulled == [[browser_image]]
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["missing_before_pull"] == [browser_image]
    assert report["pull_attempt_count"] == 1
    assert report["retried_images"] == []


def test_ensure_images_skips_pull_when_everything_is_present(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")

    module.list_local_images = lambda images: ["postgres:16"]

    def fail_pull(*_: Any, **__: Any) -> Any:
        raise AssertionError("pull_images should not be called when images are already present")

    module.pull_images = fail_pull

    rc = module.ensure_images([compose_path], parallelism=2, report_path=tmp_path / "report.json")

    assert rc == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["missing_before_pull"] == []
    assert report["pull_attempt_count"] == 0
    assert report["pulled_images"] == []


def test_ensure_images_writes_failure_report_on_pull_error(tmp_path: pathlib.Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "aux.yaml"
    compose_path.write_text("services:\n  postgres:\n    image: postgres:16\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    list_calls = iter([[], []])
    module.list_local_images = lambda images: next(list_calls)
    module.pull_images = lambda images, *, parallelism, max_attempts=3: module.PullReport(
        exit_code=1,
        pulled_images=[],
        failed_images=list(images),
        attempt_count=2,
        retried_images=list(images),
    )

    rc = module.ensure_images(
        [compose_path],
        parallelism=2,
        report_path=report_path,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert rc == 1
    assert report["failure_reason"] == "docker pull failed"
    assert report["missing_after_pull"] == ["postgres:16"]
    assert report["pull_attempt_count"] == 2
    assert report["retried_images"] == ["postgres:16"]


def test_pull_images_retries_failed_images(monkeypatch: Any) -> None:
    module = _load_module()
    attempts: dict[str, int] = {"postgres:16": 0, "qdrant:1": 0}

    def fake_pull_one_image(image: str) -> tuple[str, int, float, str]:
        attempts[image] += 1
        if image == "postgres:16" and attempts[image] == 1:
            return image, 1, 1.0, "unexpected EOF"
        return image, 0, 1.0, ""

    monkeypatch.setattr(module, "_pull_one_image", fake_pull_one_image)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    report = module.pull_images(["postgres:16", "qdrant:1"], parallelism=4, max_attempts=3)

    assert report.exit_code == 0
    assert report.attempt_count == 2
    assert report.retried_images == ["postgres:16"]
    assert report.failed_images == []
    assert sorted(report.pulled_images) == ["postgres:16", "qdrant:1"]
