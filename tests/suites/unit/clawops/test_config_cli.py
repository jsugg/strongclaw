"""Tests for StrongClaw-managed memory profile configuration."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops import config_cli


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
    output_path = tmp_path / "openclaw.json"
    installed_calls: list[str] = []

    monkeypatch.setattr(
        config_cli,
        "install_profile_assets",
        lambda repo_root, *, profile, home_dir: installed_calls.append(profile) or ["qmd"],
    )
    monkeypatch.setattr(
        config_cli,
        "render_openclaw_profile",
        lambda *, profile_name, repo_root, home_dir: {"profile": profile_name},
    )

    exit_code = config_cli.main(
        [
            "--repo-root",
            str(tmp_path),
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
    output_path = tmp_path / "openclaw.json"
    monkeypatch.setattr(
        config_cli,
        "render_openclaw_profile",
        lambda *, profile_name, repo_root, home_dir: {"profile": profile_name},
    )

    exit_code = config_cli.main(
        [
            "--repo-root",
            str(tmp_path),
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
