"""Tests for memory migration and parity tooling."""

from __future__ import annotations

import json
import pathlib
import textwrap

import pytest

from clawops.memory_tools import main as memory_main
from clawops.process_runner import CommandResult


def _write_hypermemory_config(workspace_root: pathlib.Path, config_path: pathlib.Path) -> None:
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-hypermemory.sqlite
            workspace:
              root: .
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
                - memory.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: docs
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )


def _build_workspace(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "memory").mkdir(parents=True)
    (workspace / "bank").mkdir(parents=True)
    (workspace / "MEMORY.md").write_text(
        "# Project Memory\n\n- Fact: The deploy process uses blue/green cutovers.\n",
        encoding="utf-8",
    )
    (workspace / "memory" / "2026-03-16.md").write_text(
        """
        # Daily Log

        ## Retain
        - Fact: Alice owns the deployment playbook.
        """.strip() + "\n",
        encoding="utf-8",
    )
    (workspace / "docs" / "runbook.md").write_text(
        "# Gateway Runbook\n\nRotate the gateway token before enabling a new browser profile.\n",
        encoding="utf-8",
    )
    config_path = workspace / "hypermemory.sqlite.yaml"
    _write_hypermemory_config(workspace, config_path)
    return workspace, config_path


def test_memory_migrate_hypermemory_to_pro_writes_import_and_report(
    tmp_path: pathlib.Path,
    capsys: object,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    output_path = workspace / ".runs" / "memory" / "import.json"
    report_path = workspace / ".runs" / "memory" / "migration.json"

    exit_code = memory_main(
        [
            "migrate-hypermemory-to-pro",
            "--config",
            str(config_path),
            "--scope",
            "project:strongclaw",
            "--output",
            str(output_path),
            "--report",
            str(report_path),
        ]
    )

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert summary["ok"] is True
    assert summary["importOutput"] == output_path.as_posix()
    assert output_path.exists()
    assert report_path.exists()

    export_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert export_payload["scope"] == "project:strongclaw"
    assert export_payload["memories"]


def test_memory_verify_pro_parity_uses_import_snapshot(
    tmp_path: pathlib.Path,
    capsys: object,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    snapshot_path = workspace / ".runs" / "memory" / "import.json"
    report_path = workspace / ".runs" / "memory" / "parity.json"

    migrate_exit = memory_main(
        [
            "migrate-hypermemory-to-pro",
            "--config",
            str(config_path),
            "--scope",
            "project:strongclaw",
            "--output",
            str(snapshot_path),
        ]
    )
    assert migrate_exit == 0
    _ = capsys.readouterr()

    exit_code = memory_main(
        [
            "verify-pro-parity",
            "--config",
            str(config_path),
            "--scope",
            "project:strongclaw",
            "--import-snapshot",
            str(snapshot_path),
            "--mode",
            "import",
            "--query",
            "blue/green cutovers",
            "--report",
            str(report_path),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["ok"] is True
    assert report["mode"] == "import_snapshot"
    assert "MEMORY.md" in report["queries"][0]["overlapPaths"]
    assert report_path.exists()


def test_memory_import_pro_snapshot_invokes_openclaw_cli_and_writes_report(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    snapshot_path = workspace / ".runs" / "memory" / "import.json"
    report_path = workspace / ".runs" / "memory" / "import-report.json"

    migrate_exit = memory_main(
        [
            "migrate-hypermemory-to-pro",
            "--config",
            str(config_path),
            "--scope",
            "project:strongclaw",
            "--output",
            str(snapshot_path),
        ]
    )
    assert migrate_exit == 0
    _ = capsys.readouterr()

    recorded: list[list[str]] = []

    def _fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        shell: bool = False,
    ) -> CommandResult:
        assert cwd is None
        assert env is None
        assert timeout_seconds == 120
        assert shell is False
        recorded.append(command)
        return CommandResult(
            returncode=0,
            stdout='{"imported":2,"scope":"project:strongclaw"}',
            stderr="",
            duration_ms=42,
        )

    monkeypatch.setattr("clawops.memory_tools.run_command", _fake_run_command)

    exit_code = memory_main(
        [
            "import-pro-snapshot",
            "--input",
            str(snapshot_path),
            "--report",
            str(report_path),
            "--openclaw-bin",
            "/opt/openclaw/bin/openclaw",
        ]
    )

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert recorded == [
        [
            "/opt/openclaw/bin/openclaw",
            "memory-pro",
            "import",
            snapshot_path.resolve().as_posix(),
            "--scope",
            "project:strongclaw",
        ]
    ]
    assert summary["ok"] is True
    assert summary["response"] == {"imported": 2, "scope": "project:strongclaw"}
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["command"] == recorded[0]


def test_memory_import_pro_snapshot_supports_scope_override_and_dry_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = tmp_path / "memory-pro-import.json"
    snapshot_path.write_text(
        json.dumps({"scope": "project:strongclaw", "memories": []}),
        encoding="utf-8",
    )

    recorded: list[list[str]] = []

    def _fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        shell: bool = False,
    ) -> CommandResult:
        recorded.append(command)
        return CommandResult(returncode=0, stdout="", stderr="", duration_ms=7)

    monkeypatch.setattr("clawops.memory_tools.run_command", _fake_run_command)

    exit_code = memory_main(
        [
            "import-pro-snapshot",
            "--input",
            str(snapshot_path),
            "--scope",
            "global",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert recorded == [
        [
            "openclaw",
            "memory-pro",
            "import",
            snapshot_path.resolve().as_posix(),
            "--scope",
            "global",
            "--dry-run",
        ]
    ]
    assert summary["dryRun"] is True
    assert summary["scope"] == "global"
    assert pathlib.Path(summary["report"]).exists()


def test_memory_import_pro_snapshot_reports_openclaw_start_failure(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = tmp_path / "memory-pro-import.json"
    snapshot_path.write_text(
        json.dumps({"scope": "project:strongclaw", "memories": []}),
        encoding="utf-8",
    )

    def _fake_run_command(
        command: list[str],
        *,
        cwd: pathlib.Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        shell: bool = False,
    ) -> CommandResult:
        return CommandResult(
            returncode=None,
            stdout="",
            stderr="openclaw executable not found",
            duration_ms=3,
            failed_to_start=True,
        )

    monkeypatch.setattr("clawops.memory_tools.run_command", _fake_run_command)

    exit_code = memory_main(["import-pro-snapshot", "--input", str(snapshot_path)])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 1
    assert summary["ok"] is False
    assert summary["failedToStart"] is True
    assert summary["stderrExcerpt"] == "openclaw executable not found"
