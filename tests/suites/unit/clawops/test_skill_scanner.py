"""Unit tests for the skill intake scanner."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.skill_scanner import main, scan


def _build_skill(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "handler.ts").write_text(
        "import child_process from 'node:child_process'\n", encoding="utf-8"
    )
    return root


def test_skill_scanner_finds_child_process(tmp_path: pathlib.Path) -> None:
    findings = scan(_build_skill(tmp_path))
    assert findings
    assert findings[0].rule == "js child_process"


def test_legacy_skill_scan_writes_manifest_and_quarantines(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_root = _build_skill(tmp_path)
    quarantine_root = tmp_path / "platform" / "skills" / "quarantine"
    report_path = tmp_path / "platform" / "skills" / "reports" / "scan.json"

    exit_code = main(
        [
            "--source",
            str(skill_root),
            "--quarantine",
            str(quarantine_root),
            "--report",
            str(report_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "quarantined"
    assert pathlib.Path(payload["bundlePath"]).exists()
    assert payload["bundlePath"].startswith(quarantine_root.as_posix())
    assert report_path.exists()


def test_skill_promote_and_demote_update_stage_history(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_root = _build_skill(tmp_path)
    skills_root = tmp_path / "platform" / "skills"
    report_path = skills_root / "manifests" / "bundle.json"

    scan_exit = main(
        [
            "quarantine",
            "--source",
            str(skill_root),
            "--report",
            str(report_path),
            "--quarantine-root",
            str(skills_root / "quarantine"),
        ]
    )
    assert scan_exit == 0
    quarantined = json.loads(capsys.readouterr().out)
    assert quarantined["status"] == "quarantined"

    promote_exit = main(
        [
            "promote",
            "--manifest",
            str(report_path),
            "--skills-root",
            str(skills_root),
            "--stage",
            "reviewed",
        ]
    )
    reviewed = json.loads(capsys.readouterr().out)
    assert promote_exit == 0
    assert reviewed["status"] == "reviewed"
    assert pathlib.Path(reviewed["bundlePath"]).exists()
    assert pathlib.Path(reviewed["bundlePath"]).parent.name == "reviewed"

    demote_exit = main(
        [
            "demote",
            "--manifest",
            str(report_path),
            "--skills-root",
            str(skills_root),
            "--stage",
            "quarantine",
        ]
    )
    demoted = json.loads(capsys.readouterr().out)
    assert demote_exit == 0
    assert demoted["status"] == "quarantined"
    assert pathlib.Path(demoted["bundlePath"]).parent.name == "quarantine"
    assert [entry["status"] for entry in demoted["stageHistory"]] == [
        "quarantined",
        "reviewed",
        "quarantined",
    ]
