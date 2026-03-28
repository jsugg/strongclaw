"""Unit coverage for setup/doctor runtime-boundary behavior."""

from __future__ import annotations

import pathlib

import pytest

from clawops import cli as root_cli
from clawops import setup_cli
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.assets import make_asset_root


def test_root_cli_setup_render_only_path_skips_model_auth(
    tmp_path: pathlib.Path,
    test_context: TestContext,
) -> None:
    """The public CLI should not require model auth on a render-only setup path."""
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
        del repo_root, check_only, probe
        calls.append("model")
        return {"ok": True}

    def _render_service_files(repo_root: pathlib.Path) -> dict[str, object]:
        del repo_root
        calls.append("services-render")
        return {"ok": True}

    test_context.patch.patch_object(setup_cli, "bootstrap_state_ready", new=_bootstrap_state_ready)
    test_context.patch.patch_object(
        setup_cli, "install_profile_assets", new=_install_profile_assets
    )
    test_context.patch.patch_object(setup_cli, "configure_varlock_env", new=_configure_varlock_env)
    test_context.patch.patch_object(
        setup_cli, "_render_openclaw_config", new=_render_openclaw_config
    )
    test_context.patch.patch_object(setup_cli, "_doctor_host_payload", new=_doctor_host_payload)
    test_context.patch.patch_object(setup_cli, "ensure_model_auth", new=_ensure_model_auth)
    test_context.patch.patch_object(setup_cli, "render_service_files", new=_render_service_files)

    exit_code = root_cli.main(["setup", "--asset-root", str(asset_root), "--no-activate-services"])

    assert exit_code == 0
    assert calls == [
        "assets:hypermemory",
        "varlock",
        "render:hypermemory",
        "doctor-host",
        "services-render",
    ]


def test_root_cli_doctor_bounded_path_skips_openclaw_runtime_audits(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    test_context: TestContext,
) -> None:
    """The public CLI should keep bounded doctor local when both skip flags are set."""
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
    ) -> dict[str, object]:
        del repo_root, check_only, non_interactive
        return {"ok": True}

    def _doctor_host_payload(
        repo_root: pathlib.Path,
        *,
        home_dir: pathlib.Path | None,
    ) -> dict[str, object]:
        del repo_root, home_dir
        return {"ok": True}

    def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool) -> None:
        del repo_root, probe
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

    test_context.patch.patch_object(setup_cli, "configure_varlock_env", new=_configure_varlock_env)
    test_context.patch.patch_object(setup_cli, "_doctor_host_payload", new=_doctor_host_payload)
    test_context.patch.patch_object(
        setup_cli, "_require_model_check_ok", new=_require_model_check_ok
    )
    test_context.patch.patch_object(setup_cli, "run_openclaw_command", new=_run_openclaw_command)
    test_context.patch.patch_object(setup_cli, "verify_sidecars", new=_verify_sidecars)
    test_context.patch.patch_object(setup_cli, "verify_observability", new=_verify_observability)
    test_context.patch.patch_object(setup_cli, "verify_channels", new=_verify_channels)

    exit_code = root_cli.main(
        ["doctor", "--asset-root", str(asset_root), "--skip-runtime", "--no-model-probe"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert openclaw_calls == []
    assert model_check_calls == 0
    assert "bounded local doctor" in output
