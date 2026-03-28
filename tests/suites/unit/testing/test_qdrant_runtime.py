"""Unit coverage for the managed Qdrant runtime helper."""

from __future__ import annotations

import subprocess

import pytest

from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.qdrant_runtime import (
    DEFAULT_QDRANT_IMAGE,
    QDRANT_IMAGE_ENV,
    QdrantRuntime,
)


def _docker_path(name: str) -> str:
    """Return the requested executable name for typed monkeypatching."""
    return name


def _noop_wait(url: str) -> None:
    """Treat the managed Qdrant endpoint as immediately healthy."""
    del url


def test_default_qdrant_image_uses_pinned_ghcr_mirror() -> None:
    """The managed fallback image should use the official pinned GHCR mirror."""
    assert DEFAULT_QDRANT_IMAGE == (
        "ghcr.io/qdrant/qdrant/qdrant:v1.15.5@sha256:"
        "21934642fbdc0010b3df46ab214a755fda7a4631a58beec89b050baca4c78311"
    )


def test_require_live_url_uses_repo_pinned_image_by_default(
    test_context: TestContext,
) -> None:
    """Real-mode runtimes should default to the repo-pinned Qdrant image."""
    commands: list[list[str]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id", stderr="")

    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.shutil.which", new=_docker_path)
    test_context.patch.patch(
        "tests.utils.helpers.qdrant_runtime._reserve_local_port",
        new=lambda: 46333,
    )
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime._wait_for_qdrant", new=_noop_wait)
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.subprocess.run", new=_fake_run)

    runtime = QdrantRuntime(context=None, mode="real")
    live_url = runtime.require_live_url()
    runtime.close()

    assert live_url == "http://127.0.0.1:46333"
    assert commands[0][-1] == DEFAULT_QDRANT_IMAGE
    assert commands[1][:3] == ["docker", "rm", "-f"]


def test_require_live_url_honors_configured_image_override(
    test_context: TestContext,
) -> None:
    """Real-mode runtimes should honor an explicit image override."""
    commands: list[list[str]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id", stderr="")

    test_context.env.set(QDRANT_IMAGE_ENV, "ghcr.io/example/qdrant:test")
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.shutil.which", new=_docker_path)
    test_context.patch.patch(
        "tests.utils.helpers.qdrant_runtime._reserve_local_port",
        new=lambda: 46333,
    )
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime._wait_for_qdrant", new=_noop_wait)
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.subprocess.run", new=_fake_run)

    runtime = QdrantRuntime(context=None, mode="real")
    runtime.require_live_url()
    runtime.close()

    assert commands[0][-1] == "ghcr.io/example/qdrant:test"


def test_require_live_url_skips_when_registry_access_fails(
    test_context: TestContext,
) -> None:
    """Real-mode runtimes should skip when the registry is temporarily unavailable."""

    def _fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=(
                "docker: Error response from daemon: Head "
                '"https://registry-1.docker.io/v2/qdrant/qdrant/manifests/latest": '
                "toomanyrequests: too many failed login attempts for username or IP address"
            ),
        )

    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.shutil.which", new=_docker_path)
    test_context.patch.patch(
        "tests.utils.helpers.qdrant_runtime._reserve_local_port",
        new=lambda: 46333,
    )
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.subprocess.run", new=_fake_run)

    runtime = QdrantRuntime(context=None, mode="real")

    with pytest.raises(pytest.skip.Exception, match="unable to pull Qdrant test image"):
        runtime.require_live_url()


def test_require_live_url_fails_on_non_registry_docker_errors(
    test_context: TestContext,
) -> None:
    """Real-mode runtimes should still fail for ordinary docker runtime errors."""

    def _fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del check, capture_output, text
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="docker: Error response from daemon: driver failed programming external connectivity",
        )

    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.shutil.which", new=_docker_path)
    test_context.patch.patch(
        "tests.utils.helpers.qdrant_runtime._reserve_local_port",
        new=lambda: 46333,
    )
    test_context.patch.patch("tests.utils.helpers.qdrant_runtime.subprocess.run", new=_fake_run)

    runtime = QdrantRuntime(context=None, mode="real")

    with pytest.raises(pytest.fail.Exception, match="unable to start Qdrant test container"):
        runtime.require_live_url()
