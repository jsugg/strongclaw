"""Tests for the guided StrongClaw setup and doctor entrypoints."""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from clawops import setup_cli
from tests.utils.helpers.assets import make_asset_root


def test_setup_cli_auto_skips_bootstrap_when_state_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
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
        **_kwargs: object,
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
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        calls.append("doctor-host")
        return {"ok": True}

    def _ensure_model_auth(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
        allow_prompt: bool = True,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, allow_prompt
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

    exit_code = setup_cli.setup_main(["--asset-root", str(asset_root), "--no-activate-services"])

    assert exit_code == 0
    assert calls == [
        "assets:hypermemory",
        "varlock",
        "render:hypermemory",
        "doctor-host",
        "services-render",
    ]


def test_setup_cli_keeps_model_auth_when_services_are_activated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
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
        **_kwargs: object,
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
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        calls.append("doctor-host")
        return {"ok": True}

    def _ensure_model_auth(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
        allow_prompt: bool = True,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, allow_prompt
        calls.append(f"model:{probe}")
        return {"ok": True}

    def _activate_services(repo_root: pathlib.Path) -> None:
        del repo_root
        calls.append("services-activate")

    def _verify_baseline(
        repo_root: pathlib.Path,
        *,
        runs_dir: pathlib.Path,
        degraded: bool = False,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, runs_dir
        calls.append(f"verify:{degraded}")
        return {"ok": True, "degraded": degraded}

    monkeypatch.setattr(setup_cli, "bootstrap_state_ready", _bootstrap_state_ready)
    monkeypatch.setattr(setup_cli, "install_profile_assets", _install_profile_assets)
    monkeypatch.setattr(setup_cli, "configure_varlock_env", _configure_varlock_env)
    monkeypatch.setattr(setup_cli, "_render_openclaw_config", _render_openclaw_config)
    monkeypatch.setattr(setup_cli, "_doctor_host_payload", _doctor_host_payload)
    monkeypatch.setattr(setup_cli, "ensure_model_auth", _ensure_model_auth)
    monkeypatch.setattr(setup_cli, "activate_services", _activate_services)
    monkeypatch.setattr(setup_cli, "verify_baseline", _verify_baseline)

    exit_code = setup_cli.setup_main(["--asset-root", str(asset_root), "--non-interactive"])

    assert exit_code == 0
    assert calls == [
        "assets:hypermemory",
        "varlock",
        "render:hypermemory",
        "doctor-host",
        "model:False",
        "services-activate",
        "verify:False",
    ]


def test_doctor_cli_reports_failures_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")

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
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        raise RuntimeError("env failed")

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool, **_kwargs: object) -> None:
        del repo_root, probe, _kwargs

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

    exit_code = setup_cli.doctor_main(["--asset-root", str(asset_root), "--skip-runtime"])
    payload = capsys.readouterr().out

    assert exit_code == 1
    assert "env failed" in payload


def test_doctor_cli_skips_openclaw_runtime_audits_for_bounded_local_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")

    class _OkReport:
        ok = True

        def to_dict(self) -> dict[str, object]:
            return {"ok": True}

    openclaw_calls: list[list[str]] = []
    model_check_calls = 0

    def _configure_varlock_env(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        non_interactive: bool,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        return {"ok": True}

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool, **_kwargs: object) -> None:
        del repo_root, probe, _kwargs
        nonlocal model_check_calls
        model_check_calls += 1

    def _run_openclaw_command(
        repo_root: pathlib.Path,
        arguments: list[str],
        **kwargs: object,
    ) -> object:
        del repo_root, kwargs
        openclaw_calls.append(arguments)
        return object()

    def _verify_sidecars(**kwargs: object) -> _OkReport:
        del kwargs
        return _OkReport()

    def _verify_observability(**kwargs: object) -> _OkReport:
        del kwargs
        return _OkReport()

    def _verify_channels(**kwargs: object) -> _OkReport:
        del kwargs
        return _OkReport()

    monkeypatch.setattr(setup_cli, "configure_varlock_env", _configure_varlock_env)
    monkeypatch.setattr(setup_cli, "_doctor_host_payload", _doctor_host_payload)
    monkeypatch.setattr(setup_cli, "_require_model_check_ok", _require_model_check_ok)
    monkeypatch.setattr(setup_cli, "run_openclaw_command", _run_openclaw_command)
    monkeypatch.setattr(setup_cli, "verify_sidecars", _verify_sidecars)
    monkeypatch.setattr(setup_cli, "verify_observability", _verify_observability)
    monkeypatch.setattr(setup_cli, "verify_channels", _verify_channels)

    exit_code = setup_cli.doctor_main(
        ["--asset-root", str(asset_root), "--skip-runtime", "--no-model-probe"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert openclaw_calls == []
    assert model_check_calls == 0
    assert payload["status"] == "degraded"
    assert payload["mode"] == "bounded-local"
    assert payload["counts"]["skipped"] == 4
    assert "bounded local doctor" in payload["checks"][2]["message"]


def test_doctor_cli_skip_runtime_marks_payload_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Runtime-skipped doctor runs should stay explicitly degraded."""
    asset_root = make_asset_root(tmp_path / "assets")

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
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        return {"ok": True}

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool, **_kwargs: object) -> None:
        del repo_root, probe, _kwargs

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

    monkeypatch.setattr(setup_cli, "configure_varlock_env", _configure_varlock_env)
    monkeypatch.setattr(setup_cli, "_doctor_host_payload", _doctor_host_payload)
    monkeypatch.setattr(setup_cli, "_require_model_check_ok", _require_model_check_ok)
    monkeypatch.setattr(setup_cli, "run_openclaw_command", _run_openclaw_command)
    monkeypatch.setattr(setup_cli, "verify_sidecars", _verify_sidecars)
    monkeypatch.setattr(setup_cli, "verify_observability", _verify_observability)
    monkeypatch.setattr(setup_cli, "verify_channels", _verify_channels)

    exit_code = setup_cli.doctor_main(["--asset-root", str(asset_root), "--skip-runtime"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "degraded"
    assert payload["mode"] == "runtime-skipped"
    assert payload["counts"]["failed"] == 0
    assert payload["counts"]["skipped"] == 0


def test_doctor_cli_full_runtime_includes_memory_search_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full runtime doctor runs should validate memory search readiness."""
    asset_root = make_asset_root(tmp_path / "assets")

    class _OkResult:
        ok = True

    class _OkReport:
        ok = True

        def to_dict(self) -> dict[str, object]:
            return {"ok": True}

    openclaw_calls: list[list[str]] = []

    def _configure_varlock_env(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        non_interactive: bool,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        return {"ok": True}

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool, **_kwargs: object) -> None:
        del repo_root, probe, _kwargs

    def _run_openclaw_command(
        repo_root: pathlib.Path,
        arguments: list[str],
        **kwargs: object,
    ) -> _OkResult:
        del repo_root, kwargs
        openclaw_calls.append(arguments)
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

    monkeypatch.setattr(setup_cli, "configure_varlock_env", _configure_varlock_env)
    monkeypatch.setattr(setup_cli, "_doctor_host_payload", _doctor_host_payload)
    monkeypatch.setattr(setup_cli, "_require_model_check_ok", _require_model_check_ok)
    monkeypatch.setattr(setup_cli, "run_openclaw_command", _run_openclaw_command)
    monkeypatch.setattr(setup_cli, "verify_sidecars", _verify_sidecars)
    monkeypatch.setattr(setup_cli, "verify_observability", _verify_observability)
    monkeypatch.setattr(setup_cli, "verify_channels", _verify_channels)

    exit_code = setup_cli.doctor_main(["--asset-root", str(asset_root)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "pass"
    assert ["memory", "search", "--query", "ClawOps", "--max-results", "1"] in openclaw_calls


def test_setup_cli_can_request_degraded_baseline_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Setup should pass the explicit degraded verification flag through to baseline verify."""
    asset_root = make_asset_root(tmp_path / "assets")
    calls: list[str] = []

    def _bootstrap_state_ready() -> bool:
        return True

    def _install_profile_assets(
        repo_root: pathlib.Path,
        *,
        profile: str,
        home_dir: pathlib.Path | None,
    ) -> list[str]:
        del repo_root, profile, home_dir
        return []

    def _configure_varlock_env(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        non_interactive: bool,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        return {"ok": True}

    def _render_openclaw_config(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        profile: str,
    ) -> pathlib.Path:
        del repo_root, home_dir, profile
        return tmp_path / "openclaw.json"

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _ensure_model_auth(
        repo_root: pathlib.Path,
        *,
        check_only: bool,
        probe: bool,
        allow_prompt: bool = True,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, check_only, probe, allow_prompt
        return {"ok": True}

    def _activate_services(repo_root: pathlib.Path) -> None:
        del repo_root

    def _verify_baseline(
        repo_root: pathlib.Path,
        *,
        runs_dir: pathlib.Path,
        degraded: bool = False,
        **_kwargs: object,
    ) -> dict[str, object]:
        del repo_root, runs_dir
        calls.append(f"verify:{degraded}")
        return {"ok": True, "degraded": degraded}

    monkeypatch.setattr(setup_cli, "bootstrap_state_ready", _bootstrap_state_ready)
    monkeypatch.setattr(setup_cli, "install_profile_assets", _install_profile_assets)
    monkeypatch.setattr(setup_cli, "configure_varlock_env", _configure_varlock_env)
    monkeypatch.setattr(setup_cli, "_render_openclaw_config", _render_openclaw_config)
    monkeypatch.setattr(setup_cli, "_doctor_host_payload", _doctor_host_payload)
    monkeypatch.setattr(setup_cli, "ensure_model_auth", _ensure_model_auth)
    monkeypatch.setattr(setup_cli, "activate_services", _activate_services)
    monkeypatch.setattr(setup_cli, "verify_baseline", _verify_baseline)

    exit_code = setup_cli.setup_main(
        ["--asset-root", str(asset_root), "--non-interactive", "--degraded-verify"]
    )

    assert exit_code == 0
    assert calls == ["verify:True"]
    assert "degraded mode" in capsys.readouterr().out


def test_doctor_cli_applies_requested_varlock_env_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asset_root = make_asset_root(tmp_path / "assets")
    observed_modes: list[str] = []

    class _Report:
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
        observed_modes.append(os.environ.get("STRONGCLAW_VARLOCK_ENV_MODE", ""))
        return {"ok": True}

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        observed_modes.append(os.environ.get("STRONGCLAW_VARLOCK_ENV_MODE", ""))
        return {"ok": True}

    def _verify_sidecars(**kwargs: object) -> _Report:
        del kwargs
        observed_modes.append(os.environ.get("STRONGCLAW_VARLOCK_ENV_MODE", ""))
        return _Report()

    def _verify_observability(**kwargs: object) -> _Report:
        del kwargs
        observed_modes.append(os.environ.get("STRONGCLAW_VARLOCK_ENV_MODE", ""))
        return _Report()

    def _verify_channels(**kwargs: object) -> _Report:
        del kwargs
        observed_modes.append(os.environ.get("STRONGCLAW_VARLOCK_ENV_MODE", ""))
        return _Report()

    monkeypatch.setattr(setup_cli, "configure_varlock_env", _configure_varlock_env)
    monkeypatch.setattr(setup_cli, "_doctor_host_payload", _doctor_host_payload)
    monkeypatch.setattr(setup_cli, "verify_sidecars", _verify_sidecars)
    monkeypatch.setattr(setup_cli, "verify_observability", _verify_observability)
    monkeypatch.setattr(setup_cli, "verify_channels", _verify_channels)

    exit_code = setup_cli.doctor_main(
        [
            "--asset-root",
            str(asset_root),
            "--env-mode",
            "legacy",
            "--skip-runtime",
            "--no-model-probe",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "degraded"
    assert observed_modes
    assert set(observed_modes) == {"legacy"}
