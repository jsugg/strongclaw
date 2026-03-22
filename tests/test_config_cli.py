"""Tests for StrongClaw-managed memory profile configuration."""

from __future__ import annotations

import json
import pathlib

from clawops.config_cli import main


def _write_recording_script(path: pathlib.Path, body: str) -> None:
    path.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + body,
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_memory_config_list_profiles_json(capsys: object) -> None:
    exit_code = main(["memory", "--list-profiles", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert {
        "hypermemory",
        "openclaw-default",
        "openclaw-qmd",
        "memory-pro-local",
        "memory-pro-local-smart",
    } == {entry["id"] for entry in payload["profiles"]}


def test_memory_config_set_profile_installs_qmd_and_renders(
    tmp_path: pathlib.Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    log_path = tmp_path / "config.log"
    qmd_script = tmp_path / "bootstrap_qmd.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    output_path = tmp_path / "openclaw.json"
    _write_recording_script(qmd_script, f'printf "qmd\\n" >> "{log_path}"\n')
    _write_recording_script(render_script, f'printf "render %s\\n" "$*" >> "{log_path}"\n')
    monkeypatch.setenv("CLAWOPS_CONFIG_MEMORY_BOOTSTRAP_QMD_SCRIPT", str(qmd_script))
    monkeypatch.setenv("CLAWOPS_CONFIG_MEMORY_RENDER_SCRIPT", str(render_script))

    exit_code = main(
        [
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
    assert payload["profileId"] == "openclaw-qmd"
    assert payload["renderProfile"] == "openclaw-qmd"
    assert payload["installedAssets"] == ["qmd"]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "qmd",
        f"render --profile openclaw-qmd --output {output_path.resolve()}",
    ]


def test_memory_config_set_profile_renders_openclaw_default_without_assets(
    tmp_path: pathlib.Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    log_path = tmp_path / "config.log"
    render_script = tmp_path / "render_openclaw_config.sh"
    output_path = tmp_path / "openclaw.json"
    _write_recording_script(render_script, f'printf "render %s\\n" "$*" >> "{log_path}"\n')
    monkeypatch.setenv("CLAWOPS_CONFIG_MEMORY_RENDER_SCRIPT", str(render_script))

    exit_code = main(
        [
            "memory",
            "--set-profile",
            "openclaw-default",
            "--output",
            str(output_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["profileId"] == "openclaw-default"
    assert payload["renderProfile"] == "openclaw-default"
    assert payload["installedAssets"] == []
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        f"render --profile openclaw-default --output {output_path.resolve()}",
    ]


def test_memory_config_set_profile_installs_lossless_and_renders(
    tmp_path: pathlib.Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    log_path = tmp_path / "config.log"
    lossless_script = tmp_path / "bootstrap_lossless_context_engine.sh"
    render_script = tmp_path / "render_openclaw_config.sh"
    output_path = tmp_path / "openclaw.json"
    _write_recording_script(lossless_script, f'printf "lossless\\n" >> "{log_path}"\n')
    _write_recording_script(render_script, f'printf "render %s\\n" "$*" >> "{log_path}"\n')
    monkeypatch.setenv(
        "CLAWOPS_CONFIG_MEMORY_BOOTSTRAP_LOSSLESS_CLAW_SCRIPT",
        str(lossless_script),
    )
    monkeypatch.setenv("CLAWOPS_CONFIG_MEMORY_RENDER_SCRIPT", str(render_script))

    exit_code = main(
        [
            "memory",
            "--set-profile",
            "hypermemory",
            "--output",
            str(output_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["profileId"] == "hypermemory"
    assert payload["renderProfile"] == "hypermemory"
    assert payload["installedAssets"] == ["lossless-claw"]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "lossless",
        f"render --profile hypermemory --output {output_path.resolve()}",
    ]
