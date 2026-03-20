"""Tests for rendered OpenClaw config overlays and profiles."""

from __future__ import annotations

import json
import pathlib

from clawops.app_paths import strongclaw_lossless_claw_dir
from clawops.openclaw_config import (
    render_openclaw_overlay,
    render_openclaw_profile,
    render_qmd_overlay,
)


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
        user_timezone="UTC",
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


def test_render_overlay_accepts_json5_comments_and_trailing_commas(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"
    template = tmp_path / "overlay.json5"
    template.write_text(
        """
        {
          // operator-facing note
          "memory": {
            "backend": "qmd",
            "qmd": {
              "command": "__HOME__/.bun/bin/qmd",
            },
          },
        }
        """.strip(),
        encoding="utf-8",
    )

    rendered = render_openclaw_overlay(
        template_path=template,
        repo_root=repo_root,
        home_dir=home_dir,
        user_timezone="UTC",
    )

    assert rendered["memory"]["qmd"]["command"] == f"{home_dir.resolve().as_posix()}/.bun/bin/qmd"


def test_render_overlay_accepts_full_json5_syntax(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"
    template = tmp_path / "overlay.json5"
    template.write_text(
        """
        {
          memory: {
            backend: 'qmd',
            qmd: { command: '__HOME__/.bun/bin/qmd' },
          },
        }
        """.strip(),
        encoding="utf-8",
    )

    rendered = render_openclaw_overlay(
        template_path=template,
        repo_root=repo_root,
        home_dir=home_dir,
        user_timezone="UTC",
    )

    assert rendered["memory"]["backend"] == "qmd"
    assert rendered["memory"]["qmd"]["command"] == f"{home_dir.resolve().as_posix()}/.bun/bin/qmd"


def test_repo_qmd_template_includes_expected_default_corpus() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_qmd_overlay(
        template_path=repo_root / "platform/configs/openclaw/40-qmd-context.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
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


def test_render_default_profile_merges_baseline_trust_zones_and_qmd() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_profile(
        profile_name="default",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert rendered["memory"]["backend"] == "qmd"
    assert rendered["gateway"]["bind"] == "loopback"
    admin = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "admin")
    assert admin["workspace"] == f"{repo_root.as_posix()}/platform/workspace/admin"


def test_render_acp_profile_replaces_upstream_repo_placeholders() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_profile(
        profile_name="acp",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    coder = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "coder-acp-codex")
    runtime = coder["runtime"]["acp"]
    assert runtime["cwd"] == f"{repo_root.as_posix()}/repo/upstream"
    assert coder["workspace"] == f"{repo_root.as_posix()}/repo/upstream"


def test_render_profile_accepts_additional_placeholder_backed_overlays() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_profile(
        profile_name="memory-pro-local",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
        extra_overlays=(pathlib.Path("platform/configs/openclaw/20-acp-workers.json5"),),
    )

    plugin = rendered["plugins"]["entries"]["memory-lancedb-pro"]["config"]
    coder = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "coder-acp-codex")
    assert plugin["dbPath"] == f"{pathlib.Path.home().as_posix()}/.openclaw/memory/lancedb-pro"
    assert coder["workspace"] == f"{repo_root.as_posix()}/repo/upstream"


def test_memory_v2_overlay_template_renders_repo_local_paths() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/75-strongclaw-memory-v2.example.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
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


def test_lossless_hypermemory_tier1_overlay_renders_repo_local_paths(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    home_dir = tmp_path / "home"
    lossless_dir = strongclaw_lossless_claw_dir(home_dir=home_dir)
    lossless_dir.mkdir(parents=True)
    rendered = render_openclaw_overlay(
        template_path=repo_root
        / "platform/configs/openclaw/77-lossless-hypermemory-tier1.example.json5",
        repo_root=repo_root,
        home_dir=home_dir,
        user_timezone="UTC",
    )

    assert rendered["plugins"]["slots"]["contextEngine"] == "lossless-claw"
    assert rendered["plugins"]["slots"]["memory"] == "strongclaw-memory-v2"
    assert rendered["plugins"]["load"]["paths"] == [
        lossless_dir.as_posix(),
        f"{repo_root.as_posix()}/platform/plugins/strongclaw-memory-v2",
    ]


def test_render_lossless_hypermemory_tier1_profile_merges_baseline_and_plugin_slots() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_profile(
        profile_name="lossless-hypermemory-tier1",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert rendered["gateway"]["bind"] == "loopback"
    assert rendered["plugins"]["slots"] == {
        "contextEngine": "lossless-claw",
        "memory": "strongclaw-memory-v2",
    }
    plugin_config = rendered["plugins"]["entries"]["strongclaw-memory-v2"]["config"]
    assert (
        plugin_config["configPath"]
        == f"{repo_root.as_posix()}/platform/configs/memory/memory-v2.tier1.yaml"
    )
    assert plugin_config["autoRecall"] is True
    assert plugin_config["autoReflect"] is False


def test_baseline_overlay_template_renders_workspace_and_timezone_placeholders() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/00-baseline.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    defaults = rendered["agents"]["defaults"]
    assert defaults["userTimezone"] == "UTC"
    assert defaults["workspace"] == f"{repo_root.as_posix()}/platform/workspace/admin"


def test_exec_approvals_template_renders_repo_local_prefixes() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/exec-approvals.json",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    cwd_prefixes = rendered["rules"][0]["match"]["cwdPrefixes"]
    assert cwd_prefixes == [
        repo_root.as_posix(),
        f"{repo_root.as_posix()}/repo/upstream",
    ]


def test_memory_lancedb_pro_local_overlay_renders_vendor_local_paths() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/75-clawops-memory-pro.local.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    plugin = rendered["plugins"]["entries"]["memory-lancedb-pro"]["config"]
    assert rendered["plugins"]["slots"]["memory"] == "memory-lancedb-pro"
    assert rendered["plugins"]["load"]["paths"] == [
        f"{repo_root.as_posix()}/platform/plugins/memory-lancedb-pro"
    ]
    assert plugin["dbPath"] == f"{pathlib.Path.home().as_posix()}/.openclaw/memory/lancedb-pro"
    assert plugin["sessionStrategy"] == "none"
    assert plugin["selfImprovement"]["enabled"] is False
    assert plugin["smartExtraction"] is False


def test_memory_lancedb_pro_local_smart_overlay_enables_local_llm_only() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_overlay(
        template_path=repo_root
        / "platform/configs/openclaw/76-clawops-memory-pro.local-smart.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    plugin = rendered["plugins"]["entries"]["memory-lancedb-pro"]["config"]
    assert plugin["smartExtraction"] is True
    assert plugin["llm"]["baseURL"] == "http://127.0.0.1:11434/v1"
    assert plugin["selfImprovement"]["enabled"] is False


def test_vendored_memory_lancedb_pro_bundle_is_pinned() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    package = json.loads(
        (repo_root / "platform/plugins/memory-lancedb-pro/package.json").read_text(encoding="utf-8")
    )
    vendor_note = (
        repo_root / "platform/plugins/memory-lancedb-pro/STRONGCLAW_VENDOR.md"
    ).read_text(encoding="utf-8")

    assert package["version"] == "1.1.0-beta.9"
    assert "2ebba8e6b7b65bf38336199384d5ec8690701f6e" in vendor_note
    assert "selfImprovement.enabled = false" in vendor_note
