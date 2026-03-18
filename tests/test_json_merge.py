"""Regression tests for merge-json overlay behavior."""

from __future__ import annotations

import json
import pathlib

from clawops.json_merge import main


def test_merge_json_accepts_full_json5_overlay_syntax(tmp_path: pathlib.Path) -> None:
    base_path = tmp_path / "base.json"
    overlay_path = tmp_path / "overlay.json5"
    output_path = tmp_path / "merged.json"
    base_path.write_text('{"memory": {"backend": "local"}}\n', encoding="utf-8")
    overlay_path.write_text(
        """
        {
          memory: {
            backend: 'qmd',
            qmd: { command: '/tmp/qmd' },
          },
        }
        """.strip(),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--base",
            str(base_path),
            "--overlay",
            str(overlay_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "memory": {"backend": "qmd", "qmd": {"command": "/tmp/qmd"}}
    }
