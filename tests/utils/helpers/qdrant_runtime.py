"""Managed Qdrant runtime helpers for hypermemory tests."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from typing import Any

import pytest
import requests

from tests.utils.helpers.hypermemory import FakeQdrantBackend
from tests.utils.helpers.mode import ServiceMode
from tests.utils.helpers.test_context import TestContext

QDRANT_URL_ENV = "TEST_QDRANT_URL"

type CleanupFn = Callable[[], None]


def _wait_for_qdrant(url: str) -> None:
    """Wait for a Qdrant HTTP endpoint to report healthy."""
    last_error: Exception | None = None
    for endpoint in ("readyz", "healthz"):
        for _ in range(30):
            try:
                response = requests.get(f"{url.rstrip('/')}/{endpoint}", timeout=1.0)
                if endpoint == "readyz" and response.status_code == 404:
                    break
                response.raise_for_status()
                return
            except requests.RequestException as err:
                last_error = err
                time.sleep(1.0)
        if endpoint == "healthz":
            break
        last_error = RuntimeError(f"{url} does not expose /readyz")
    detail = "unknown error" if last_error is None else str(last_error)
    raise RuntimeError(f"Qdrant did not become healthy at {url}: {detail}")


def _reserve_local_port() -> int:
    """Reserve an ephemeral localhost port for a temporary Qdrant container."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class QdrantRuntime:
    """Resolve mock-or-real Qdrant behavior and track lifecycle cleanup."""

    def __init__(self, context: TestContext | None, mode: ServiceMode) -> None:
        self._context = context
        self.mode = mode
        self._cleanup_stack: list[tuple[str, CleanupFn]] = []
        self._live_url: str | None = None
        self._collection_name: str | None = None
        self._session: requests.Session | None = None

    def __enter__(self) -> QdrantRuntime:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    @property
    def collection_name(self) -> str:
        """Return a unique collection name for the current test context."""
        if self._collection_name is None:
            if self._context is None:
                self._collection_name = "test_runtime"
            else:
                self._collection_name = f"test_{self._context.resource_prefix}"
        return self._collection_name

    def close(self) -> None:
        """Run locally managed cleanup actions in reverse order."""
        while self._cleanup_stack:
            _, cleanup = self._cleanup_stack.pop()
            cleanup()

    def _register_cleanup(self, name: str, fn: CleanupFn) -> None:
        if self._context is not None:
            self._context.register_cleanup(name, fn)
            return
        self._cleanup_stack.append((name, fn))

    def connect(self) -> Any:
        """Return a Qdrant client suited to the selected mode."""
        if self.mode == "mock":
            return FakeQdrantBackend()
        if self._session is None:
            self._session = requests.Session()
            self._register_cleanup("qdrant:session", self._session.close)
        return self._session

    def require_live_url(self) -> str:
        """Return a reachable Qdrant URL, starting a local container if needed."""
        if self.mode == "mock":
            pytest.skip("Qdrant mock mode is active; live Qdrant runtime is not required.")
        if self._live_url is not None:
            return self._live_url

        qdrant_url = os.environ.get(QDRANT_URL_ENV, "").strip()
        if qdrant_url:
            _wait_for_qdrant(qdrant_url)
            self._live_url = qdrant_url
            return qdrant_url

        docker_bin = shutil.which("docker")
        if docker_bin is None:
            pytest.skip(
                f"{QDRANT_URL_ENV} is unset and docker is unavailable; "
                "real Qdrant tests require one of them."
            )

        port = _reserve_local_port()
        container_name = f"strongclaw-qdrant-{self.collection_name}"[:63]
        result = subprocess.run(
            [
                docker_bin,
                "run",
                "--rm",
                "-d",
                "--name",
                container_name,
                "-p",
                f"{port}:6333",
                "qdrant/qdrant",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "docker run failed"
            pytest.fail(f"unable to start Qdrant test container: {detail}")

        def _cleanup_container() -> None:
            subprocess.run(
                [docker_bin, "rm", "-f", container_name],
                check=False,
                capture_output=True,
                text=True,
            )

        self._register_cleanup(
            f"qdrant:container:{container_name}",
            _cleanup_container,
        )
        self._live_url = f"http://127.0.0.1:{port}"
        _wait_for_qdrant(self._live_url)
        return self._live_url

    def prepare_collection(self, prefix: str = "test") -> str:
        """Return a unique collection name and register cleanup for real-mode runs."""
        collection = f"{prefix}_{self.collection_name}"[:63]
        if self.mode == "real":
            self.register_collection_cleanup(collection)
        return collection

    def register_collection_cleanup(self, collection_name: str) -> None:
        """Delete a real Qdrant collection during teardown when it exists."""
        base_url = self.require_live_url()
        session = self.connect()
        assert isinstance(session, requests.Session)

        def _cleanup() -> None:
            response = session.delete(
                f"{base_url.rstrip('/')}/collections/{collection_name}",
                timeout=5.0,
            )
            if response.status_code not in {200, 202, 404}:
                response.raise_for_status()

        self._register_cleanup(f"qdrant:collection:{collection_name}", _cleanup)
