"""Contracts for tracked patch cleanup."""

from __future__ import annotations

from tests.utils.helpers.patches import PatchManager
from tests.utils.helpers.test_context import TestContext

TARGET_VALUE = "before"


class _Target:
    attribute = "before"


def test_patches_are_stopped_on_context_cleanup() -> None:
    ctx = TestContext()
    manager = PatchManager(ctx)

    manager.patch(f"{__name__}.TARGET_VALUE", new="after")
    assert TARGET_VALUE == "after"

    ctx.cleanup_all()

    assert TARGET_VALUE == "before"


def test_object_patches_are_stopped_on_context_cleanup() -> None:
    ctx = TestContext()
    manager = PatchManager(ctx)

    manager.patch_object(_Target, "attribute", new="after")
    assert _Target.attribute == "after"

    ctx.cleanup_all()

    assert _Target.attribute == "before"


def test_dict_patches_are_stopped_on_context_cleanup() -> None:
    ctx = TestContext()
    manager = PatchManager(ctx)
    payload = {"mode": "before"}

    manager.patch_dict(payload, {"mode": "after"})
    assert payload["mode"] == "after"

    ctx.cleanup_all()

    assert payload["mode"] == "before"


def test_patch_cleanup_runs_before_resource_cleanup() -> None:
    events: list[str] = []
    ctx = TestContext()
    manager = PatchManager(ctx)

    manager.patch_dict({"mode": "before"}, {"mode": "after"})
    ctx.register_cleanup("resource", lambda: events.append("resource"))
    ctx.register_patch_cleanup("patch", lambda: events.append("patch"))

    ctx.cleanup_all()

    assert events == ["patch", "resource"]
