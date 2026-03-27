"""Tests for supply-chain inventory and refresh tooling."""

from __future__ import annotations

import pathlib

import pytest

from clawops.process_runner import CommandResult
from clawops.supply_chain import (
    inventory_pins,
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
    monkeypatch.setattr(
        "clawops.supply_chain.requests.get",
        lambda *args, **kwargs: _FakeResponse(sha="89abcdef0123456789abcdef0123456789abcdef"),
    )

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
    monkeypatch.setattr(
        "clawops.supply_chain._run_docker_inspect",
        lambda image: CommandResult(
            returncode=0,
            stdout="Name: postgres:16-alpine\nDigest: sha256:2222222222222222222222222222222222222222222222222222222222222222\n",
            stderr="",
            duration_ms=5,
        ),
    )

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
        if command[:2] == ["uv", "run"] and "pytest" in command:
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
