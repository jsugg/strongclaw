"""Unit tests for the skill scanner."""

from __future__ import annotations

import pathlib

from clawops.skill_scanner import scan


def test_skill_scanner_finds_child_process(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "handler.ts").write_text("import child_process from 'node:child_process'\n", encoding="utf-8")
    findings = scan(root)
    assert findings
    assert findings[0].rule == "js child_process"
