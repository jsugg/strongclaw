"""Unit tests for worker-aware test identity helpers."""

from __future__ import annotations

from tests.utils.helpers.identity import get_worker_id, make_resource_prefix, make_test_id


def test_make_test_id_is_unique_across_calls() -> None:
    assert make_test_id() != make_test_id()


def test_make_test_id_contains_worker_id(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    assert make_test_id().endswith("_gw3")


def test_make_test_id_defaults_to_main_worker(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert make_test_id().endswith("_main")


def test_make_resource_prefix_contains_nanoseconds(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    parts = make_resource_prefix().split("_")

    assert parts[1] == "gw1"
    assert parts[2].isdigit()


def test_make_resource_prefix_embeds_worker(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw2")
    assert "_gw2_" in make_resource_prefix()


def test_get_worker_id_reads_xdist_env(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw4")
    assert get_worker_id() == "gw4"


def test_get_worker_id_defaults_to_main(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert get_worker_id() == "main"
