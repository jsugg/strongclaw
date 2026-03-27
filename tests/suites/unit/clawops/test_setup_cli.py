"""Tests for the guided StrongClaw setup and doctor entrypoints."""

from __future__ import annotations

import pathlib

import pytest

from clawops import setup_cli


def test_setup_cli_auto_skips_bootstrap_when_state_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    calls: list[str] = []

    def _bootstrap_state_ready() -> bool:
        return True

    def _install_profile_assets(
        repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path | None,
    ) -> list[str]:
        del repo_root, home_dir
        calls.append(f"assets:{profile}")
        return []

    def _configure_varlock_env(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        non_interactive: bool,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        calls.append("varlock")
        return {"ok": True}

    def _render_openclaw_config(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        profile: str,
    ) -> pathlib.Path:
        del repo_root, home_dir
        calls.append(f"render:{profile}")
        return tmp_path / "openclaw.json"

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        calls.append("doctor-host")
        return {"ok": True}

    def _ensure_model_auth(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
    ) -> dict[str, object]:
        del repo_root, check_only
        calls.append(f"model:{probe}")
        return {"ok": True}

    def _render_service_files(repo_root: pathlib.Path) -> dict[str, object]:
        del repo_root
        calls.append("services-render")
        return {"ok": True}

    monkeypatch.setattr(setup_cli, "bootstrap_state_ready", _bootstrap_state_ready)
    monkeypatch.setattr(
        setup_cli,
        "install_profile_assets",
        _install_profile_assets,
    )
    monkeypatch.setattr(
        setup_cli,
        "configure_varlock_env",
        _configure_varlock_env,
    )
    monkeypatch.setattr(
        setup_cli,
        "_render_openclaw_config",
        _render_openclaw_config,
    )
    monkeypatch.setattr(
        setup_cli,
        "_doctor_host_payload",
        _doctor_host_payload,
    )
    monkeypatch.setattr(
        setup_cli,
        "ensure_model_auth",
        _ensure_model_auth,
    )
    monkeypatch.setattr(
        setup_cli,
        "render_service_files",
        _render_service_files,
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _OkResult:
        ok = True

    class _OkReport:
        ok = True

        def to_dict(self) -> dict[str, object]:
            return {"ok": True}

    def _configure_varlock_env(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        non_interactive: bool,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        raise RuntimeError("env failed")

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool) -> None:
        del repo_root, probe

    def _run_openclaw_command(
        repo_root: pathlib.Path,
        arguments: list[str],
        **kwargs: object,
    ) -> _OkResult:
        del repo_root, arguments, kwargs
        return _OkResult()

    def _verify_sidecars(**kwargs: object) -> _OkReport:
        del kwargs
        return _OkReport()

    def _verify_observability(**kwargs: object) -> _OkReport:
        del kwargs
        return _OkReport()

    def _verify_channels(**kwargs: object) -> _OkReport:
        del kwargs
        return _OkReport()

    monkeypatch.setattr(
        setup_cli,
        "configure_varlock_env",
        _configure_varlock_env,
    )
    monkeypatch.setattr(
        setup_cli,
        "_doctor_host_payload",
        _doctor_host_payload,
    )
    monkeypatch.setattr(
        setup_cli,
        "_require_model_check_ok",
        _require_model_check_ok,
    )
    monkeypatch.setattr(
        setup_cli,
        "run_openclaw_command",
        _run_openclaw_command,
    )
    monkeypatch.setattr(
        setup_cli,
        "verify_sidecars",
        _verify_sidecars,
    )
    monkeypatch.setattr(
        setup_cli,
        "verify_observability",
        _verify_observability,
    )
    monkeypatch.setattr(
        setup_cli,
        "verify_channels",
        _verify_channels,
    )

    exit_code = setup_cli.doctor_main(["--repo-root", str(tmp_path), "--skip-runtime"])
    payload = capsys.readouterr().out

    assert exit_code == 1
    assert "env failed" in payload
