"""Tests for supply-chain inventory, quality gates, and refresh tooling."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.process_runner import CommandResult
from clawops.supply_chain import (
    inventory_pins,
    main,
    propose_refresh,
    refresh_compose_image_digests,
    refresh_workflow_action_pins,
)


class _FakeResponse:
    def __init__(self, *, sha: str) -> None:
        self._sha = sha

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"sha": self._sha}


def test_inventory_collects_workflow_and_compose_pins(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    workflow_dir = repo_root / ".github" / "workflows"
    compose_dir = repo_root / "platform" / "compose"
    workflow_dir.mkdir(parents=True)
    compose_dir.mkdir(parents=True)
    (workflow_dir / "security.yml").write_text(
        "steps:\n  - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v5\n",
        encoding="utf-8",
    )
    (compose_dir / "stack.yaml").write_text(
        "services:\n  app:\n    image: postgres:16-alpine@sha256:1111111111111111111111111111111111111111111111111111111111111111\n",
        encoding="utf-8",
    )

    payload = inventory_pins(repo_root)

    assert payload["ok"] is True
    assert payload["workflowActions"] == [
        {
            "action": "actions/checkout",
            "line": 2,
            "path": ".github/workflows/security.yml",
            "ref": "0123456789abcdef0123456789abcdef01234567",
            "tag": "v5",
        }
    ]
    assert payload["composeImages"] == [
        {
            "digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "image": "postgres:16-alpine",
            "line": 3,
            "path": "platform/compose/stack.yaml",
        }
    ]


def test_refresh_workflow_action_pins_rewrites_outdated_sha(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    workflow_dir = repo_root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow_path = workflow_dir / "security.yml"
    workflow_path.write_text(
        "steps:\n  - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v5\n",
        encoding="utf-8",
    )

    def _fake_requests_get(*args: object, **kwargs: object) -> _FakeResponse:
        del args, kwargs
        return _FakeResponse(sha="89abcdef0123456789abcdef0123456789abcdef")

    monkeypatch.setattr("clawops.supply_chain.requests.get", _fake_requests_get)

    payload = refresh_workflow_action_pins(repo_root, apply=True)

    assert payload["updated"] == [
        {
            "action": "actions/checkout",
            "from": "0123456789abcdef0123456789abcdef01234567",
            "line": 2,
            "path": ".github/workflows/security.yml",
            "tag": "v5",
            "to": "89abcdef0123456789abcdef0123456789abcdef",
        }
    ]
    assert "actions/checkout@89abcdef0123456789abcdef0123456789abcdef" in workflow_path.read_text(
        encoding="utf-8"
    )


def test_refresh_compose_image_digests_rewrites_outdated_digest(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    compose_dir = repo_root / "platform" / "compose"
    compose_dir.mkdir(parents=True)
    compose_path = compose_dir / "stack.yaml"
    compose_path.write_text(
        "services:\n  app:\n    image: postgres:16-alpine@sha256:1111111111111111111111111111111111111111111111111111111111111111\n",
        encoding="utf-8",
    )

    def _fake_run_docker_inspect(image: str) -> CommandResult:
        del image
        return CommandResult(
            returncode=0,
            stdout="Name: postgres:16-alpine\nDigest: sha256:2222222222222222222222222222222222222222222222222222222222222222\n",
            stderr="",
            duration_ms=5,
        )

    monkeypatch.setattr("clawops.supply_chain._run_docker_inspect", _fake_run_docker_inspect)

    payload = refresh_compose_image_digests(repo_root, apply=True)

    assert payload["updated"] == [
        {
            "from": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "image": "postgres:16-alpine",
            "line": 3,
            "path": "platform/compose/stack.yaml",
            "to": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        }
    ]
    assert (
        "postgres:16-alpine@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        in compose_path.read_text(encoding="utf-8")
    )


def test_quality_gate_cli_runs_declared_commands(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    commands: list[list[str]] = []
    envs: list[dict[str, str] | None] = []

    def _fake_run_command(
        command: list[str] | str,
        *,
        cwd: pathlib.Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        shell: bool = False,
    ) -> CommandResult:
        del timeout_seconds, shell
        assert cwd == repo_root
        assert isinstance(command, list)
        commands.append(command)
        envs.append(env)
        return CommandResult(returncode=0, stdout="ok", stderr="", duration_ms=1)

    monkeypatch.setattr("clawops.supply_chain.run_command", _fake_run_command)

    exit_code = main(["--repo-root", str(repo_root), "quality-gate"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["repoRoot"] == repo_root.as_posix()
    assert payload["commands"] == commands
    assert commands == [
        ["uv", "run", "pre-commit", "run", "actionlint", "--all-files"],
        ["uv", "run", "pre-commit", "run", "shellcheck", "--all-files"],
        ["uv", "run", "isort", "--check-only", "src", "tests"],
        ["uv", "run", "ruff", "check", "src", "tests"],
        ["uv", "run", "black", "--check", "src", "tests"],
        ["uv", "run", "pyright"],
        ["uv", "run", "mypy"],
        [
            "python3",
            "./tests/scripts/launch_readiness.py",
            "generate-audit-packet",
            "--output-dir",
            ".tmp/launch-readiness/audit-packet",
        ],
        [
            "bash",
            "-lc",
            "export STRONGCLAW_LAUNCH_READINESS_ARTIFACT_MODE=live; "
            "export STRONGCLAW_LAUNCH_READINESS_ARTIFACT_ROOT=.tmp/launch-readiness/audit-packet; "
            "ulimit -n 4096 && uv run pytest -q --junitxml=pytest.xml "
            "--cov=src/clawops --cov-report=xml --cov-report=term-missing",
        ],
        [
            "python3",
            "./tests/scripts/security_workflow.py",
            "enforce-coverage-thresholds",
            "--coverage-file",
            "coverage.xml",
        ],
        ["uv", "run", "python", "-m", "compileall", "-q", "src", "tests"],
    ]
    assert all(env is not None for env in envs)
    assert all(env["PYTHONPATH"] == "src" for env in envs if env is not None)
    assert all(env["CLAWOPS_HTTP_RETRY_MODE"] == "safe" for env in envs if env is not None)


def test_propose_refresh_runs_quality_gate_sbom_commit_and_pr(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state = {"status_calls": 0}

    def _fake_run_command(
        command: list[str] | str,
        *,
        cwd: pathlib.Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        shell: bool = False,
    ) -> CommandResult:
        del cwd, env, timeout_seconds, shell
        if isinstance(command, str):
            return CommandResult(returncode=0, stdout="", stderr="", duration_ms=1)
        if command[:4] == ["git", "-C", str(repo_root), "status"]:
            state["status_calls"] += 1
            stdout = "" if state["status_calls"] == 1 else "M .github/workflows/security.yml\n"
            return CommandResult(returncode=0, stdout=stdout, stderr="", duration_ms=1)
        if command[:4] == ["git", "-C", str(repo_root), "rev-parse"]:
            return CommandResult(returncode=1, stdout="", stderr="missing", duration_ms=1)
        if command[:4] == ["git", "-C", str(repo_root), "switch"]:
            return CommandResult(returncode=0, stdout="", stderr="", duration_ms=1)
        if command[:4] == ["git", "-C", str(repo_root), "add"]:
            return CommandResult(returncode=0, stdout="", stderr="", duration_ms=1)
        if command[:4] == ["git", "-C", str(repo_root), "commit"]:
            return CommandResult(returncode=0, stdout="[branch] refresh", stderr="", duration_ms=1)
        if command[:3] == ["uv", "run", "pre-commit"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:3] == ["uv", "run", "isort"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:3] == ["uv", "run", "ruff"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:3] == ["uv", "run", "black"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:3] == ["uv", "run", "pyright"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:3] == ["uv", "run", "mypy"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:3] == [
            "python3",
            "./tests/scripts/launch_readiness.py",
            "generate-audit-packet",
        ]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:2] == ["bash", "-lc"] and "uv run pytest" in command[-1]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:2] == ["python3", "./tests/scripts/security_workflow.py"]:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:2] == ["uv", "run"] and "compileall" in command:
            return CommandResult(returncode=0, stdout="quality ok", stderr="", duration_ms=1)
        if command[:2] == ["syft", "dir:."]:
            pathlib.Path(str(command[-1]).split("=", 1)[1]).write_text("{}", encoding="utf-8")
            return CommandResult(returncode=0, stdout="sbom ok", stderr="", duration_ms=1)
        if command[:3] == ["gh", "pr", "create"]:
            return CommandResult(
                returncode=0,
                stdout="https://github.com/jsugg/strongclaw/pull/123\n",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("clawops.supply_chain.run_command", _fake_run_command)

    payload = propose_refresh(
        repo_root,
        branch="chore/supply-chain-refresh",
        base_branch="main",
        create_pr=True,
        refresh_actions=False,
        refresh_compose_digests_enabled=False,
        refresh_commands=[],
        commit_message="chore: refresh",
        title="Refresh pins",
        body="Refresh pins",
        dry_run=False,
    )

    assert payload["ok"] is True
    assert payload["branch"] == "chore/supply-chain-refresh"
    assert payload["changedFiles"] == [".github/workflows/security.yml"]
    assert payload["prUrl"] == "https://github.com/jsugg/strongclaw/pull/123"
    assert pathlib.Path(str(payload["sbomPath"])).exists()
