"""Tests for memory migration and parity tooling."""

from __future__ import annotations

import json
import pathlib
import textwrap

from clawops.memory_tools import main as memory_main


def _write_memory_v2_config(workspace_root: pathlib.Path, config_path: pathlib.Path) -> None:
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-memory-v2.sqlite
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
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    return workspace, config_path


def test_memory_migrate_v2_to_pro_writes_import_and_report(
    tmp_path: pathlib.Path,
    capsys: object,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    output_path = workspace / ".runs" / "memory" / "import.json"
    report_path = workspace / ".runs" / "memory" / "migration.json"

    exit_code = memory_main(
        [
            "migrate-v2-to-pro",
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
            "migrate-v2-to-pro",
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
