"""Tests for rendered OpenClaw memory config overlays."""

from __future__ import annotations

import pathlib

from clawops.openclaw_config import render_openclaw_overlay, render_qmd_overlay


def test_render_qmd_overlay_replaces_local_placeholders(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"
    template = tmp_path / "40-qmd-context.json"
    template.write_text(
        """
        {
          "memory": {
            "backend": "qmd",
            "qmd": {
              "command": "__HOME__/.bun/bin/qmd",
              "paths": [
                {"name": "docs", "path": "__REPO_ROOT__/platform/docs", "pattern": "**/*.md"}
              ]
            }
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    rendered = render_qmd_overlay(
        template_path=template,
        repo_root=repo_root,
        home_dir=home_dir,
    )

    qmd = rendered["memory"]["qmd"]
    assert qmd["command"] == f"{home_dir.resolve().as_posix()}/.bun/bin/qmd"
    assert qmd["paths"] == [
        {
            "name": "docs",
            "path": f"{repo_root.resolve().as_posix()}/platform/docs",
            "pattern": "**/*.md",
        }
    ]


def test_repo_qmd_template_includes_expected_default_corpus() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_qmd_overlay(
        template_path=repo_root / "platform/configs/openclaw/40-qmd-context.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
    )

    assert rendered["memory"]["backend"] == "qmd"
    qmd = rendered["memory"]["qmd"]
    assert qmd["includeDefaultMemory"] is True
    path_names = {entry["name"] for entry in qmd["paths"]}
    assert {
        "runbooks",
        "skills",
        "readme",
        "quickstart",
        "setup-guide",
        "usage-guide",
        "project-memory",
        "shared-memory",
    } <= path_names


def test_memory_v2_overlay_template_renders_repo_local_paths() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/75-strongclaw-memory-v2.example.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
    )

    plugin_config = rendered["plugins"]["entries"]["strongclaw-memory-v2"]["config"]
    assert rendered["plugins"]["slots"]["memory"] == "strongclaw-memory-v2"
    assert (
        plugin_config["configPath"]
        == f"{repo_root.as_posix()}/platform/configs/memory/memory-v2.yaml"
    )
    assert rendered["plugins"]["load"]["paths"] == [
        f"{repo_root.as_posix()}/platform/plugins/strongclaw-memory-v2"
    ]
