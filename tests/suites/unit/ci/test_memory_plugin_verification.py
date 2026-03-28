"""Unit coverage for memory-plugin verification helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.plugins.infrastructure.context import TestContext
from tests.scripts import memory_plugin_verification as memory_plugin_script
from tests.utils.helpers import ci_workflows
from tests.utils.helpers._ci_workflows import memory_plugin as memory_plugin_helpers


def test_run_vendored_host_checks_installs_cli_and_clears_aws_env(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """Vendored host checks should prefix PATH and clear ambient AWS variables."""
    repo_root = tmp_path / "repo"
    plugin_dir = repo_root / "platform" / "plugins" / "memory-lancedb-pro"
    plugin_dir.mkdir(parents=True)
    seen_calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []

    def fake_run_checked(
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        capture_output: bool = False,
    ) -> Any:
        del timeout_seconds, capture_output
        seen_calls.append((command, cwd, env))
        return None

    test_context.patch.patch_object(memory_plugin_helpers, "run_checked", new=fake_run_checked)
    test_context.env.set("PATH", "/usr/bin")
    test_context.env.set("AWS_PROFILE", "default")
    test_context.env.set("AWS_REGION", "us-east-1")

    ci_workflows.run_vendored_host_checks(repo_root)

    install_command, install_cwd, install_env = seen_calls[0]
    assert install_command[:4] == ["npm", "install", "--prefix", install_command[3]]
    assert install_cwd == repo_root.resolve()
    assert install_env is None

    _, npm_ci_cwd, npm_ci_env = seen_calls[1]
    assert npm_ci_cwd == plugin_dir.resolve()
    assert npm_ci_env is not None
    assert "AWS_PROFILE" not in npm_ci_env
    assert "AWS_REGION" not in npm_ci_env
    assert npm_ci_env["PATH"].startswith(str(Path(install_command[3]) / "node_modules" / ".bin"))


def test_wait_for_qdrant_retries_until_ready(test_context: TestContext) -> None:
    """Qdrant readiness should retry transient probe failures."""
    attempts: list[str] = []
    sleeps: list[float] = []

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb
            return None

    def fake_urlopen(url: str, timeout: int) -> _Response:
        attempts.append(url)
        assert timeout == 5
        if len(attempts) < 3:
            raise OSError("not ready")
        return _Response()

    test_context.patch.patch_object(
        memory_plugin_helpers.urllib.request,
        "urlopen",
        new=fake_urlopen,
    )
    test_context.patch.patch_object(memory_plugin_helpers.time, "sleep", new=sleeps.append)

    ci_workflows.wait_for_qdrant("http://127.0.0.1:6333/healthz", attempts=4, sleep_seconds=1.5)

    assert attempts == ["http://127.0.0.1:6333/healthz"] * 3
    assert sleeps == [1.5, 1.5]


def test_main_dispatches_wait_for_qdrant(test_context: TestContext) -> None:
    """The CLI should dispatch Qdrant readiness checks."""
    seen_calls: list[tuple[str, int, float]] = []

    def fake_wait_for_qdrant(url: str, *, attempts: int = 30, sleep_seconds: float = 2.0) -> None:
        seen_calls.append((url, attempts, sleep_seconds))

    test_context.patch.patch_object(
        memory_plugin_script,
        "wait_for_qdrant",
        new=fake_wait_for_qdrant,
    )

    exit_code = memory_plugin_script.main(
        ["wait-for-qdrant", "--url", "http://127.0.0.1:6333/healthz", "--attempts", "12"]
    )

    assert exit_code == 0
    assert seen_calls == [("http://127.0.0.1:6333/healthz", 12, 2.0)]
