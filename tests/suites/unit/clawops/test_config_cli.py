"""Tests for StrongClaw-managed memory profile configuration."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops import config_cli
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.assets import make_asset_root


def test_memory_config_list_profiles_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = config_cli.main(["memory", "--list-profiles", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert {
        "hypermemory",
        "openclaw-default",
        "openclaw-qmd",
        "memory-lancedb-pro",
    } == {entry["id"] for entry in payload["profiles"]}


def test_memory_config_set_profile_installs_assets_and_renders(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
    output_path = tmp_path / "openclaw.json"
    installed_calls: list[str] = []

    def _install_profile_assets(
        repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path | None,
    ) -> list[str]:
        del repo_root, home_dir
        installed_calls.append(profile)
        return ["qmd"]

    def _render_openclaw_profile(
        *,
        profile_name: str,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"profile": profile_name}

    monkeypatch.setattr(
        config_cli,
        "install_profile_assets",
        _install_profile_assets,
    )
    monkeypatch.setattr(
        config_cli,
        "render_openclaw_profile",
        _render_openclaw_profile,
    )

    def _test_materialize_runtime_memory_configs(
        *,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path,
        user_timezone: str | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        del repo_root, home_dir, user_timezone
        return tmp_path / "hypermemory.yaml", tmp_path / "hypermemory.sqlite.yaml"

    monkeypatch.setattr(
        config_cli,
        "materialize_runtime_memory_configs",
        _test_materialize_runtime_memory_configs,
    )

    exit_code = config_cli.main(
        [
            "--asset-root",
            str(asset_root),
            "memory",
            "--set-profile",
            "openclaw-qmd",
            "--output",
            str(output_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert installed_calls == ["openclaw-qmd"]
    assert payload["installedAssets"] == ["qmd"]
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"profile": "openclaw-qmd"}


def test_memory_config_set_profile_skip_assets_only_renders(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
    output_path = tmp_path / "openclaw.json"

    def _render_openclaw_profile(
        *,
        profile_name: str,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"profile": profile_name}

    monkeypatch.setattr(
        config_cli,
        "render_openclaw_profile",
        _render_openclaw_profile,
    )

    def _test_materialize_runtime_memory_configs(
        *,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path,
        user_timezone: str | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        del repo_root, home_dir, user_timezone
        return tmp_path / "hypermemory.yaml", tmp_path / "hypermemory.sqlite.yaml"

    monkeypatch.setattr(
        config_cli,
        "materialize_runtime_memory_configs",
        _test_materialize_runtime_memory_configs,
    )

    exit_code = config_cli.main(
        [
            "--asset-root",
            str(asset_root),
            "memory",
            "--set-profile",
            "hypermemory",
            "--skip-assets",
            "--output",
            str(output_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["installedAssets"] == []
    assert payload["renderProfile"] == "hypermemory"


def test_memory_config_parse_args_defers_output_default() -> None:
    args = config_cli.parse_args(["memory", "--list-profiles"])

    assert args.output is None


def test_memory_config_default_output_uses_runtime_layout(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
    runtime_root = tmp_path / "dev-runtime"
    test_context.env.set("STRONGCLAW_RUNTIME_ROOT", str(runtime_root))

    def _render_openclaw_profile(
        *,
        profile_name: str,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"profile": profile_name}

    def _test_materialize_runtime_memory_configs(
        *,
        repo_root: pathlib.Path,
        home_dir: pathlib.Path,
        user_timezone: str | None = None,
    ) -> tuple[pathlib.Path, pathlib.Path]:
        del repo_root, home_dir, user_timezone
        return tmp_path / "hypermemory.yaml", tmp_path / "hypermemory.sqlite.yaml"

    test_context.patch.patch_object(
        config_cli,
        "render_openclaw_profile",
        new=_render_openclaw_profile,
    )
    test_context.patch.patch_object(
        config_cli,
        "materialize_runtime_memory_configs",
        new=_test_materialize_runtime_memory_configs,
    )

    exit_code = config_cli.main(
        [
            "--asset-root",
            str(asset_root),
            "memory",
            "--set-profile",
            "hypermemory",
            "--skip-assets",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["output"] == str(runtime_root / ".openclaw" / "openclaw.json")
