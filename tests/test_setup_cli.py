"""Tests for the guided StrongClaw setup and doctor entrypoints."""

from __future__ import annotations

import pathlib

from clawops import setup_cli


def test_setup_cli_auto_skips_bootstrap_when_state_exists(
    monkeypatch: object, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(setup_cli, "bootstrap_state_ready", lambda: True)
    monkeypatch.setattr(
        setup_cli,
        "install_profile_assets",
        lambda repo_root, *, profile, home_dir: calls.append(f"assets:{profile}") or [],
    )
    monkeypatch.setattr(
        setup_cli,
        "configure_varlock_env",
        lambda repo_root, *, check_only, non_interactive: calls.append("varlock") or {"ok": True},
    )
    monkeypatch.setattr(
        setup_cli,
        "_render_openclaw_config",
        lambda repo_root, *, home_dir, profile: calls.append(f"render:{profile}")
        or tmp_path / "openclaw.json",
    )
    monkeypatch.setattr(
        setup_cli,
        "_doctor_host_payload",
        lambda repo_root, *, home_dir: calls.append("doctor-host") or {"ok": True},
    )
    monkeypatch.setattr(
        setup_cli,
        "ensure_model_auth",
        lambda repo_root, *, check_only, probe: calls.append(f"model:{probe}") or {"ok": True},
    )
    monkeypatch.setattr(
        setup_cli,
        "render_service_files",
        lambda repo_root: calls.append("services-render") or {"ok": True},
    )

    exit_code = setup_cli.setup_main(["--repo-root", str(tmp_path), "--no-activate-services"])

    assert exit_code == 0
    assert calls == [
        "assets:hypermemory",
        "varlock",
        "render:hypermemory",
        "doctor-host",
        "model:True",
        "services-render",
    ]


def test_doctor_cli_reports_failures_without_raising(
    monkeypatch: object, tmp_path: pathlib.Path, capsys: object
) -> None:
    monkeypatch.setattr(
        setup_cli,
        "configure_varlock_env",
        lambda repo_root, *, check_only, non_interactive: (_ for _ in ()).throw(
            RuntimeError("env failed")
        ),
    )
    monkeypatch.setattr(
        setup_cli,
        "_doctor_host_payload",
        lambda repo_root, *, home_dir: {"ok": True},
    )
    monkeypatch.setattr(
        setup_cli,
        "_require_model_check_ok",
        lambda repo_root, *, probe: None,
    )
    monkeypatch.setattr(
        setup_cli,
        "run_openclaw_command",
        lambda repo_root, arguments, **kwargs: type("Result", (), {"ok": True})(),
    )
    monkeypatch.setattr(
        setup_cli,
        "verify_sidecars",
        lambda **kwargs: type("Report", (), {"ok": True, "to_dict": lambda self: {"ok": True}})(),
    )
    monkeypatch.setattr(
        setup_cli,
        "verify_observability",
        lambda **kwargs: type("Report", (), {"ok": True, "to_dict": lambda self: {"ok": True}})(),
    )
    monkeypatch.setattr(
        setup_cli,
        "verify_channels",
        lambda **kwargs: type("Report", (), {"ok": True, "to_dict": lambda self: {"ok": True}})(),
    )

    exit_code = setup_cli.doctor_main(["--repo-root", str(tmp_path), "--skip-runtime"])
    payload = capsys.readouterr().out

    assert exit_code == 1
    assert "env failed" in payload
