"""Unit coverage for provisioning OpenClaw's Docker sandbox image."""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from clawops import openclaw_sandbox
from clawops.strongclaw_runtime import CommandError
from tests.plugins.infrastructure.context import TestContext


class _FakeDocker:
    """Record docker invocations and serve one image label."""

    def __init__(self, test_context: TestContext, *, label: str | None, build_rc: int = 0) -> None:
        self.label = label
        self.build_rc = build_rc
        self.builds: list[list[str]] = []
        self.dockerfiles: list[str] = []
        test_context.patch.patch_object(openclaw_sandbox, "run_command", new=self.inspect)
        test_context.patch.patch_object(openclaw_sandbox, "run_command_inherited", new=self.build)

    def inspect(self, command: list[str], **_: object) -> SimpleNamespace:
        assert command[:3] == ["docker", "image", "inspect"]
        assert command[-1] == openclaw_sandbox.SANDBOX_IMAGE
        if self.label is None:
            return SimpleNamespace(ok=False, stdout="", stderr="No such image")
        return SimpleNamespace(ok=True, stdout=f"{self.label}\n", stderr="")

    def build(self, command: list[str], **_: object) -> int:
        self.builds.append(command)
        dockerfile = pathlib.Path(command[command.index("--file") + 1])
        self.dockerfiles.append(dockerfile.read_text(encoding="utf-8"))
        return self.build_rc


@pytest.mark.parametrize(
    "label",
    [None, "<no value>", "0000000000000000"],
    ids=["missing-image", "unlabeled-retagged-base", "older-recipe"],
)
def test_sandbox_image_is_built_when_missing_or_stale(
    test_context: TestContext, label: str | None
) -> None:
    """A missing image, the 2026.3 retagged base, or an older recipe all trigger a build."""
    docker = _FakeDocker(test_context, label=label)

    assert openclaw_sandbox.ensure_openclaw_sandbox_image() is True

    recipe = openclaw_sandbox.sandbox_recipe_digest()
    [command] = docker.builds
    assert command[:2] == ["docker", "build"]
    assert f"{openclaw_sandbox.SANDBOX_IMAGE_LABEL}={recipe}" in command
    assert command[command.index("--tag") + 1] == "openclaw-sandbox:bookworm-slim"
    assert docker.dockerfiles == [openclaw_sandbox.SANDBOX_DOCKERFILE]


def test_current_sandbox_image_is_left_alone(test_context: TestContext) -> None:
    """Re-running bootstrap or setup does not rebuild an up-to-date image."""
    docker = _FakeDocker(test_context, label=openclaw_sandbox.sandbox_recipe_digest())

    assert openclaw_sandbox.ensure_openclaw_sandbox_image() is False
    assert docker.builds == []


def test_failed_sandbox_build_is_reported(test_context: TestContext) -> None:
    """A failed build stops setup instead of leaving agents without a sandbox."""
    _FakeDocker(test_context, label=None, build_rc=1)

    with pytest.raises(CommandError, match="openclaw-sandbox:bookworm-slim"):
        openclaw_sandbox.ensure_openclaw_sandbox_image()


def test_sandbox_recipe_pins_its_base_and_ships_python() -> None:
    """The base is digest-pinned and the image carries python3 for the write/edit helpers."""
    first_line = openclaw_sandbox.SANDBOX_DOCKERFILE.splitlines()[0]

    assert first_line.startswith("FROM debian:bookworm-slim@sha256:")
    assert len(first_line.rsplit(":", 1)[1]) == 64
    assert " python3 " in openclaw_sandbox.SANDBOX_DOCKERFILE
    assert "USER sandbox" in openclaw_sandbox.SANDBOX_DOCKERFILE
