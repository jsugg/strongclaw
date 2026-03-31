"""Tests for rendered OpenClaw config overlays and profiles."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops.app_paths import (
    strongclaw_lossless_claw_dir,
    strongclaw_memory_config_dir,
    strongclaw_plugin_dir,
    strongclaw_upstream_repo_dir,
    strongclaw_workspace_dir,
)
from clawops.openclaw_config import (
    DEV_RUNTIME_GATEWAY_PORT,
    main,
    materialize_runtime_memory_configs,
    parse_args,
    render_openclaw_overlay,
    render_openclaw_profile,
    render_qmd_overlay,
)
from clawops.strongclaw_runtime import managed_python
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT


def test_render_qmd_overlay_replaces_local_placeholders(tmp_path: pathlib.Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"
    (repo_root / "platform").mkdir(parents=True)
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
    (repo_root / "platform").mkdir(parents=True)
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
    (repo_root / "platform").mkdir(parents=True)
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
    repo_root = REPO_ROOT
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
        "openclaw-workspaces",
        "openclaw-upstream",
        "repo-root-markdown",
    } <= path_names


def test_render_openclaw_default_profile_merges_baseline_and_trust_zones() -> None:
    repo_root = REPO_ROOT
    workspace_root = strongclaw_workspace_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_profile(
        profile_name="openclaw-default",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert "backend" not in rendered["memory"]
    assert rendered["gateway"]["bind"] == "loopback"
    assert rendered["plugins"]["slots"]["memory"] == "memory-core"
    admin = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "admin")
    assert admin["workspace"] == f"{workspace_root.as_posix()}/admin"


def test_render_openclaw_qmd_profile_merges_baseline_trust_zones_and_qmd() -> None:
    repo_root = REPO_ROOT
    workspace_root = strongclaw_workspace_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_profile(
        profile_name="openclaw-qmd",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert rendered["memory"]["backend"] == "qmd"
    assert rendered["gateway"]["bind"] == "loopback"
    admin = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "admin")
    assert admin["workspace"] == f"{workspace_root.as_posix()}/admin"


def test_render_acp_profile_replaces_upstream_repo_placeholders() -> None:
    repo_root = REPO_ROOT
    upstream_root = strongclaw_upstream_repo_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_profile(
        profile_name="acp",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    coder = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "coder-acp-codex")
    runtime = coder["runtime"]["acp"]
    assert runtime["cwd"] == upstream_root.as_posix()
    assert coder["workspace"] == upstream_root.as_posix()


def test_render_profile_accepts_additional_placeholder_backed_overlays() -> None:
    repo_root = REPO_ROOT
    upstream_root = strongclaw_upstream_repo_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_profile(
        profile_name="memory-lancedb-pro",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
        extra_overlays=(pathlib.Path("platform/configs/openclaw/20-acp-workers.json5"),),
    )

    plugin = rendered["plugins"]["entries"]["memory-lancedb-pro"]["config"]
    coder = next(agent for agent in rendered["agents"]["list"] if agent["id"] == "coder-acp-codex")
    assert plugin["dbPath"] == f"{pathlib.Path.home().as_posix()}/.openclaw/memory/lancedb-pro"
    assert coder["workspace"] == upstream_root.as_posix()


def test_hypermemory_overlay_template_renders_repo_local_paths() -> None:
    repo_root = REPO_ROOT
    memory_config_dir = strongclaw_memory_config_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_overlay(
        template_path=repo_root
        / "platform/configs/openclaw/75-strongclaw-hypermemory.example.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    plugin_config = rendered["plugins"]["entries"]["strongclaw-hypermemory"]["config"]
    assert rendered["plugins"]["slots"]["memory"] == "strongclaw-hypermemory"
    assert plugin_config["command"] == [managed_python(repo_root).as_posix(), "-m", "clawops"]
    assert plugin_config["configPath"] == f"{memory_config_dir.as_posix()}/hypermemory.sqlite.yaml"
    assert rendered["plugins"]["load"]["paths"] == [
        f"{repo_root.as_posix()}/platform/plugins/strongclaw-hypermemory"
    ]


def test_hypermemory_overlay_renders_repo_local_paths(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = REPO_ROOT
    home_dir = tmp_path / "home"
    lossless_dir = strongclaw_lossless_claw_dir(home_dir=home_dir)
    lossless_dir.mkdir(parents=True)
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/77-hypermemory.example.json5",
        repo_root=repo_root,
        home_dir=home_dir,
        user_timezone="UTC",
    )

    assert rendered["plugins"]["slots"]["contextEngine"] == "lossless-claw"
    assert rendered["plugins"]["slots"]["memory"] == "strongclaw-hypermemory"
    assert rendered["plugins"]["load"]["paths"] == [
        lossless_dir.as_posix(),
        f"{repo_root.as_posix()}/platform/plugins/strongclaw-hypermemory",
    ]


def test_render_hypermemory_profile_merges_baseline_and_plugin_slots() -> None:
    repo_root = REPO_ROOT
    memory_config_dir = strongclaw_memory_config_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_profile(
        profile_name="hypermemory",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert rendered["gateway"]["bind"] == "loopback"
    assert rendered["plugins"]["slots"] == {
        "contextEngine": "lossless-claw",
        "memory": "strongclaw-hypermemory",
    }
    plugin_config = rendered["plugins"]["entries"]["strongclaw-hypermemory"]["config"]
    assert plugin_config["configPath"] == f"{memory_config_dir.as_posix()}/hypermemory.yaml"
    assert plugin_config["command"] == [managed_python(repo_root).as_posix(), "-m", "clawops"]
    assert plugin_config["autoRecall"] is True
    assert plugin_config["autoReflect"] is False


def test_baseline_overlay_template_renders_workspace_and_timezone_placeholders() -> None:
    repo_root = REPO_ROOT
    workspace_root = strongclaw_workspace_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/00-baseline.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    defaults = rendered["agents"]["defaults"]
    assert defaults["userTimezone"] == "UTC"
    assert defaults["workspace"] == f"{workspace_root.as_posix()}/admin"


def test_exec_approvals_template_renders_repo_local_prefixes() -> None:
    repo_root = REPO_ROOT
    upstream_root = strongclaw_upstream_repo_dir(home_dir=pathlib.Path.home())
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/exec-approvals.json",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    cwd_prefixes = rendered["rules"][0]["match"]["cwdPrefixes"]
    assert cwd_prefixes == [
        repo_root.as_posix(),
        upstream_root.as_posix(),
    ]


def test_memory_lancedb_pro_overlay_renders_vendor_local_paths() -> None:
    repo_root = REPO_ROOT
    plugin_root = strongclaw_plugin_dir("memory-lancedb-pro", home_dir=pathlib.Path.home())
    rendered = render_openclaw_overlay(
        template_path=repo_root / "platform/configs/openclaw/75-memory-lancedb-pro.local.json5",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    plugin = rendered["plugins"]["entries"]["memory-lancedb-pro"]["config"]
    assert rendered["plugins"]["slots"]["memory"] == "memory-lancedb-pro"
    assert rendered["plugins"]["load"]["paths"] == [plugin_root.as_posix()]
    assert plugin["dbPath"] == f"{pathlib.Path.home().as_posix()}/.openclaw/memory/lancedb-pro"
    assert plugin["sessionStrategy"] == "none"
    assert plugin["selfImprovement"]["enabled"] is False
    assert plugin["smartExtraction"] is True
    assert plugin["llm"]["baseURL"] == "http://127.0.0.1:11434/v1"
    assert plugin["selfImprovement"]["enabled"] is False


def test_vendored_memory_lancedb_pro_bundle_is_pinned() -> None:
    repo_root = REPO_ROOT
    package = json.loads(
        (repo_root / "platform/plugins/memory-lancedb-pro/package.json").read_text(encoding="utf-8")
    )
    vendor_note = (
        repo_root / "platform/plugins/memory-lancedb-pro/STRONGCLAW_VENDOR.md"
    ).read_text(encoding="utf-8")

    assert package["version"] == "1.1.0-beta.10"
    assert "63495671fde55f2c8e3d6eb95267381d1889cca9" in vendor_note
    assert "selfImprovement.enabled = false" in vendor_note


def test_materialize_runtime_memory_configs_writes_managed_configs(
    tmp_path: pathlib.Path,
) -> None:
    repo_root = REPO_ROOT
    home_dir = tmp_path / "home"

    default_path, sqlite_path = materialize_runtime_memory_configs(
        repo_root=repo_root,
        home_dir=home_dir,
        user_timezone="UTC",
    )

    default_text = default_path.read_text(encoding="utf-8")
    sqlite_text = sqlite_path.read_text(encoding="utf-8")
    workspace_root = strongclaw_workspace_dir(home_dir=home_dir)
    upstream_root = strongclaw_upstream_repo_dir(home_dir=home_dir)

    assert "__REPO_ROOT__" not in default_text
    assert "__WORKSPACE_ROOT__" not in sqlite_text
    assert repo_root.as_posix() in default_text
    assert workspace_root.as_posix() in default_text
    assert upstream_root.as_posix() in sqlite_text


def test_parse_args_defers_openclaw_config_output_default() -> None:
    args = parse_args(["--profile", "hypermemory"])

    assert args.output is None


def test_render_openclaw_profile_injects_dev_gateway_port_when_runtime_root_is_active(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    rendered = render_openclaw_profile(
        profile_name="hypermemory",
        repo_root=REPO_ROOT,
        home_dir=tmp_path / "home",
        user_timezone="UTC",
    )

    assert rendered["gateway"]["port"] == DEV_RUNTIME_GATEWAY_PORT
    assert rendered["agents"]["defaults"]["workspace"] == str(
        runtime_root / "strongclaw" / "data" / "workspace" / "admin"
    )


def test_main_uses_layout_derived_output_when_output_is_omitted(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    exit_code = main(["--asset-root", str(REPO_ROOT), "--profile", "hypermemory"])

    rendered_path = runtime_root / ".openclaw" / "openclaw.json"
    payload = json.loads(rendered_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["gateway"]["port"] == DEV_RUNTIME_GATEWAY_PORT
    assert f"Rendered {rendered_path}" in capsys.readouterr().out
