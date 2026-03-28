"""CLI entrypoints for guided StrongClaw setup and doctor workflows."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from collections.abc import Callable

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.common import write_json
from clawops.openclaw_config import materialize_runtime_memory_configs, render_openclaw_profile
from clawops.platform_verify import verify_channels, verify_observability, verify_sidecars
from clawops.runtime_assets import resolve_asset_path
from clawops.strongclaw_baseline import verify_baseline
from clawops.strongclaw_bootstrap import bootstrap_host, install_profile_assets
from clawops.strongclaw_model_auth import ensure_model_auth
from clawops.strongclaw_runtime import (
    CommandError,
    bootstrap_state_ready,
    clear_docker_shell_refresh_required,
    command_exists,
    docker_backend_ready,
    docker_shell_refresh_required,
    load_openclaw_config,
    rendered_openclaw_hypermemory_config_path,
    rendered_openclaw_lossless_plugin_path,
    rendered_openclaw_uses_hypermemory,
    rendered_openclaw_uses_lossless_claw,
    rendered_openclaw_uses_qmd,
    require_openclaw,
    resolve_home_dir,
    resolve_openclaw_config_path,
    resolve_profile,
    resolve_runtime_user,
    resolve_varlock_bin,
    run_command,
    run_openclaw_command,
)
from clawops.strongclaw_services import activate_services, render_service_files
from clawops.strongclaw_varlock_env import configure_varlock_env


def _render_openclaw_config(
    repo_root: pathlib.Path, *, home_dir: pathlib.Path, profile: str
) -> pathlib.Path:
    """Render the selected OpenClaw profile."""
    output_path = resolve_openclaw_config_path(repo_root, home_dir=home_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_openclaw_profile(
        profile_name=profile,
        repo_root=repo_root,
        home_dir=home_dir,
    )
    materialize_runtime_memory_configs(repo_root=repo_root, home_dir=home_dir)
    write_json(output_path, rendered)
    return output_path


def _pause_for_linux_docker_refresh(repo_root: pathlib.Path) -> None:
    """Stop setup until the operator opens a fresh login shell."""
    runtime_user = resolve_runtime_user(repo_root)
    raise CommandError(
        "Docker access was granted during bootstrap, but this shell has not picked up the new "
        f"docker-group membership yet. Open a fresh login shell as {runtime_user}, then rerun `clawops setup`."
    )


def _setup_parser(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse setup arguments."""
    parser = argparse.ArgumentParser(description="Run the guided StrongClaw setup workflow.")
    add_asset_root_argument(parser)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    parser.add_argument("--profile")
    parser.add_argument("--skip-bootstrap", action="store_true")
    parser.add_argument("--force-bootstrap", action="store_true")
    parser.add_argument("--no-activate-services", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    return parser.parse_args(argv)


def _doctor_parser(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse doctor arguments."""
    parser = argparse.ArgumentParser(description="Run a deep StrongClaw readiness scan.")
    add_asset_root_argument(parser)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--no-model-probe", action="store_true")
    return parser.parse_args(argv)


def _doctor_host_parser(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse doctor-host arguments."""
    parser = argparse.ArgumentParser(description="Run the host-only StrongClaw readiness scan.")
    add_asset_root_argument(parser)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    return parser.parse_args(argv)


def _doctor_host_payload(repo_root: pathlib.Path, *, home_dir: pathlib.Path) -> dict[str, object]:
    """Validate the host toolchain and rendered config."""
    require_openclaw("Bootstrap doctor requires the OpenClaw CLI.")
    if not command_exists("acpx"):
        raise CommandError("Bootstrap doctor requires the ACPX CLI.")
    if resolve_varlock_bin() is None:
        raise CommandError("Bootstrap doctor requires the Varlock CLI.")
    config_path = resolve_openclaw_config_path(repo_root, home_dir=home_dir)
    if not config_path.exists():
        raise CommandError(f"Rendered OpenClaw config not found at {config_path}.")
    validate_result = run_openclaw_command(
        repo_root,
        ["config", "validate"],
        timeout_seconds=120,
    )
    if not validate_result.ok:
        detail = (
            validate_result.stderr.strip()
            or validate_result.stdout.strip()
            or "openclaw config validate failed"
        )
        raise CommandError(detail, result=validate_result)
    load_openclaw_config(config_path)
    payload: dict[str, object] = {
        "ok": True,
        "config": str(config_path),
        "openclawVersion": run_command(
            ["openclaw", "--version"], timeout_seconds=15
        ).stdout.strip(),
        "acpxVersion": run_command(["acpx", "--version"], timeout_seconds=15).stdout.strip(),
        "varlockVersion": run_command(
            [str(resolve_varlock_bin()), "--version"], timeout_seconds=15
        ).stdout.strip(),
    }
    if rendered_openclaw_uses_qmd(config_path):
        qmd_bin = pathlib.Path.home() / ".bun" / "bin" / "qmd"
        if not qmd_bin.exists():
            raise CommandError(
                f"Bootstrap doctor requires the QMD semantic memory backend at {qmd_bin}."
            )
        payload["qmdBin"] = str(qmd_bin)
    if rendered_openclaw_uses_lossless_claw(config_path):
        lossless_path = rendered_openclaw_lossless_plugin_path(config_path)
        if lossless_path is None or not (lossless_path / "openclaw.plugin.json").exists():
            raise CommandError(
                "Rendered config enables lossless-claw, but the plugin path is missing or invalid."
            )
        payload["losslessClawPath"] = str(lossless_path)
    if rendered_openclaw_uses_hypermemory(config_path):
        hypermemory_config_path = rendered_openclaw_hypermemory_config_path(config_path)
        if hypermemory_config_path is None or not hypermemory_config_path.exists():
            raise CommandError(
                "strongclaw-hypermemory is enabled, but its configPath is missing or unreadable."
            )
        payload["hypermemoryConfig"] = str(hypermemory_config_path)
    return payload


def doctor_host_main(argv: list[str] | None = None) -> int:
    """Run the host-only StrongClaw doctor."""
    args = _doctor_host_parser(argv)
    payload = _doctor_host_payload(
        resolve_asset_root_argument(args, command_name="clawops doctor-host"),
        home_dir=resolve_home_dir(args.home_dir),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run_check(
    label: str,
    remediation: str,
    checks: list[dict[str, object]],
    action: Callable[[], object],
) -> None:
    """Run one readiness check and store the result."""
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": label, "ok": False, "message": str(exc), "remediation": remediation})
        return
    checks.append({"name": label, "ok": True, "message": "ok", "remediation": remediation})


def _record_skipped_check(
    label: str,
    remediation: str,
    checks: list[dict[str, object]],
    *,
    reason: str,
) -> None:
    """Record an intentionally skipped readiness check."""
    checks.append(
        {"name": label, "ok": True, "message": f"skipped: {reason}", "remediation": remediation}
    )


def _require_model_check_ok(repo_root: pathlib.Path, *, probe: bool) -> None:
    """Raise when the model-auth check payload reports failure."""
    payload = ensure_model_auth(repo_root, check_only=True, probe=probe)
    if not bool(payload.get("ok")):
        raise CommandError(str(payload.get("guidance", "OpenClaw model readiness failed.")))


def _setup_requires_model_auth(*, activate_services_enabled: bool) -> bool:
    """Return whether setup must validate model auth before continuing."""
    return activate_services_enabled


def _bounded_local_doctor(args: argparse.Namespace) -> bool:
    """Return whether doctor should stay on local, non-runtime checks only."""
    return bool(args.skip_runtime and args.no_model_probe)


def setup_main(argv: list[str] | None = None) -> int:
    """Run the guided StrongClaw setup workflow."""
    args = _setup_parser(argv)
    repo_root = resolve_asset_root_argument(args, command_name="clawops setup")
    home_dir = resolve_home_dir(args.home_dir)
    profile = resolve_profile(args.profile)
    if args.skip_bootstrap and args.force_bootstrap:
        raise SystemExit("--skip-bootstrap and --force-bootstrap cannot be used together.")
    skip_bootstrap = bool(args.skip_bootstrap)
    if not skip_bootstrap and not args.force_bootstrap and bootstrap_state_ready():
        skip_bootstrap = True
    if not skip_bootstrap or bool(args.force_bootstrap):
        bootstrap_host(repo_root, profile=profile, home_dir=home_dir)
    else:
        install_profile_assets(repo_root, profile=profile, home_dir=home_dir)
    configure_varlock_env(
        repo_root,
        check_only=False,
        non_interactive=bool(args.non_interactive),
    )
    if args.profile or skip_bootstrap:
        _render_openclaw_config(repo_root, home_dir=home_dir, profile=profile)
        _doctor_host_payload(repo_root, home_dir=home_dir)
    activate_services_enabled = not bool(args.no_activate_services)
    verify_enabled = not bool(args.no_verify)
    if not activate_services_enabled:
        verify_enabled = False
    model_auth_deferred = not _setup_requires_model_auth(
        activate_services_enabled=activate_services_enabled
    )
    if not model_auth_deferred:
        model_payload = ensure_model_auth(
            repo_root,
            check_only=False,
            probe=not bool(args.non_interactive),
        )
        if not bool(model_payload.get("ok")):
            raise CommandError(str(model_payload.get("guidance", "OpenClaw model auth failed.")))
    if (
        activate_services_enabled
        and os.uname().sysname == "Linux"
        and docker_shell_refresh_required()
    ):
        if docker_backend_ready():
            clear_docker_shell_refresh_required()
        else:
            _pause_for_linux_docker_refresh(repo_root)
    if activate_services_enabled:
        activate_services(repo_root)
    else:
        render_service_files(repo_root)
    if verify_enabled:
        verify_baseline(repo_root, runs_dir=repo_root / ".tmp" / "harness")
    next_steps = [
        "- Control UI: http://127.0.0.1:18789/",
        "- Deep health scan: clawops doctor",
    ]
    if model_auth_deferred:
        next_steps.append(
            "- Model/provider auth was deferred because services were not activated; "
            "run `clawops model-auth ensure` before starting the gateway."
        )
    print("StrongClaw setup completed.\n\n" "Next steps:\n" f"{'\n'.join(next_steps)}\n")
    return 0


def doctor_main(argv: list[str] | None = None) -> int:
    """Run the deep StrongClaw readiness scan."""
    args = _doctor_parser(argv)
    repo_root = resolve_asset_root_argument(args, command_name="clawops doctor")
    home_dir = resolve_home_dir(args.home_dir)
    checks: list[dict[str, object]] = []
    _run_check(
        "Varlock env contract",
        "clawops varlock-env configure",
        checks,
        lambda: configure_varlock_env(repo_root, check_only=True, non_interactive=True),
    )
    _run_check(
        "Host toolchain and rendered config",
        "clawops setup",
        checks,
        lambda: _doctor_host_payload(repo_root, home_dir=home_dir),
    )
    if not args.skip_runtime and os.uname().sysname == "Linux" and docker_shell_refresh_required():
        _run_check(
            "Linux docker session refresh",
            "Open a fresh login shell, then rerun clawops setup",
            checks,
            lambda: (
                clear_docker_shell_refresh_required()
                if docker_backend_ready()
                else _pause_for_linux_docker_refresh(repo_root)
            ),
        )
    if _bounded_local_doctor(args):
        skip_reason = "--skip-runtime and --no-model-probe requested a bounded local doctor"
        _record_skipped_check(
            "OpenClaw model readiness",
            "clawops model-auth ensure",
            checks,
            reason=skip_reason,
        )
        _record_skipped_check(
            "OpenClaw doctor",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw doctor --non-interactive",
            checks,
            reason=skip_reason,
        )
        _record_skipped_check(
            "OpenClaw security audit",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw security audit --deep",
            checks,
            reason=skip_reason,
        )
        _record_skipped_check(
            "OpenClaw secrets audit",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw secrets audit --check",
            checks,
            reason=skip_reason,
        )
    else:
        _run_check(
            "OpenClaw model readiness",
            "clawops model-auth ensure",
            checks,
            lambda: _require_model_check_ok(
                repo_root,
                probe=not bool(args.skip_runtime or args.no_model_probe),
            ),
        )
        _run_check(
            "OpenClaw doctor",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw doctor --non-interactive",
            checks,
            lambda: run_openclaw_command(
                repo_root, ["doctor", "--non-interactive"], timeout_seconds=300, check=True
            ),
        )
        _run_check(
            "OpenClaw security audit",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw security audit --deep",
            checks,
            lambda: run_openclaw_command(
                repo_root, ["security", "audit", "--deep"], timeout_seconds=300, check=True
            ),
        )
        _run_check(
            "OpenClaw secrets audit",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw secrets audit --check",
            checks,
            lambda: run_openclaw_command(
                repo_root, ["secrets", "audit", "--check"], timeout_seconds=300, check=True
            ),
        )
    if not args.skip_runtime:
        _run_check(
            "OpenClaw gateway status",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw gateway status --json",
            checks,
            lambda: run_openclaw_command(
                repo_root, ["gateway", "status", "--json"], timeout_seconds=300, check=True
            ),
        )
        _run_check(
            "OpenClaw memory status",
            "OPENCLAW_GATEWAY_TOKEN=<token> openclaw memory status --deep",
            checks,
            lambda: run_openclaw_command(
                repo_root, ["memory", "status", "--deep"], timeout_seconds=300, check=True
            ),
        )
    sidecars_report = verify_sidecars(
        compose_path=resolve_asset_path(
            "platform/compose/docker-compose.aux-stack.yaml",
            repo_root=repo_root,
        ),
        skip_runtime=bool(args.skip_runtime),
    )
    checks.append(
        {
            "name": "Platform sidecars",
            "ok": sidecars_report.ok,
            "message": json.dumps(sidecars_report.to_dict()),
            "remediation": "clawops verify-platform sidecars",
        }
    )
    observability_report = verify_observability(
        overlay_path=resolve_asset_path(
            "platform/configs/openclaw/50-observability.json5",
            repo_root=repo_root,
        ),
        compose_path=resolve_asset_path(
            "platform/compose/docker-compose.aux-stack.yaml",
            repo_root=repo_root,
        ),
        skip_runtime=bool(args.skip_runtime),
    )
    checks.append(
        {
            "name": "Platform observability",
            "ok": observability_report.ok,
            "message": json.dumps(observability_report.to_dict()),
            "remediation": "clawops verify-platform observability",
        }
    )
    channels_report = verify_channels(
        overlay_path=resolve_asset_path(
            "platform/configs/openclaw/30-channels.json5",
            repo_root=repo_root,
        ),
        channels_doc_path=resolve_asset_path("platform/docs/CHANNELS.md", repo_root=repo_root),
        telegram_guidance_path=resolve_asset_path(
            "platform/docs/channels/telegram.md",
            repo_root=repo_root,
        ),
        whatsapp_guidance_path=resolve_asset_path(
            "platform/docs/channels/whatsapp.md",
            repo_root=repo_root,
        ),
        allowlist_source_path=resolve_asset_path(
            "platform/configs/source-allowlists.example.yaml",
            repo_root=repo_root,
        ),
    )
    checks.append(
        {
            "name": "Platform channels",
            "ok": channels_report.ok,
            "message": json.dumps(channels_report.to_dict()),
            "remediation": "clawops verify-platform channels",
        }
    )
    payload = {"ok": all(bool(check["ok"]) for check in checks), "checks": checks}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1
