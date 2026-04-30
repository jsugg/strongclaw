"""Render and activate StrongClaw host service definitions."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import time
from typing import Final
from xml.sax.saxutils import escape

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.common import load_text, write_text
from clawops.observability import emit_structured_log
from clawops.platform_compat import detect_host_platform, resolve_service_manager
from clawops.runtime_assets import RuntimeLayout, resolve_runtime_layout
from clawops.strongclaw_runtime import (
    ensure_docker_backend_ready,
    managed_python,
    resolve_openclaw_config_path,
    resolve_openclaw_state_dir,
    resolve_repo_root,
    run_command,
)

LAUNCHD_ACTIVATE_LABELS: Final[tuple[str, ...]] = (
    "ai.openclaw.sidecars",
    "ai.openclaw.gateway",
    "ai.openclaw.backup-create",
    "ai.openclaw.backup-verify",
    "ai.openclaw.maintenance",
)
LAUNCHD_GATEWAY_LABEL: Final[str] = "ai.openclaw.gateway"
LAUNCHD_SIDECARS_LABEL: Final[str] = "ai.openclaw.sidecars"
LAUNCHD_BACKUP_CREATE_LABEL: Final[str] = "ai.openclaw.backup-create"
LAUNCHD_BACKUP_VERIFY_LABEL: Final[str] = "ai.openclaw.backup-verify"
LAUNCHD_MAINTENANCE_LABEL: Final[str] = "ai.openclaw.maintenance"
LAUNCHD_GATEWAY_TIMEOUT_ENV_VAR: Final[str] = "STRONGCLAW_LAUNCHD_GATEWAY_TIMEOUT_SECONDS"
LAUNCHD_SIDECARS_TIMEOUT_ENV_VAR: Final[str] = "STRONGCLAW_LAUNCHD_SIDECARS_TIMEOUT_SECONDS"
LAUNCHD_PASSTHROUGH_ENV_VARS: Final[tuple[str, ...]] = (
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "STRONGCLAW_COMPOSE_VARIANT",
)
LAUNCHD_GATEWAY_TIMEOUT_SECONDS: Final[int] = 30
LAUNCHD_SIDECARS_TIMEOUT_SECONDS: Final[int] = 1800
LAUNCHD_ONESHOT_MAX_ATTEMPTS: Final[int] = 2
LAUNCHD_ONESHOT_RETRY_DELAY_SECONDS: Final[int] = 2
SYSTEMD_ACTIVATE_UNITS: Final[tuple[str, ...]] = (
    "openclaw-sidecars.service",
    "openclaw-gateway.service",
    "openclaw-backup-create.timer",
    "openclaw-backup-verify.timer",
    "openclaw-maintenance.timer",
)


def launchd_dir() -> pathlib.Path:
    """Return the default launchd user-agent directory."""
    return pathlib.Path.home() / "Library" / "LaunchAgents"


def systemd_dir() -> pathlib.Path:
    """Return the default user-level systemd directory."""
    return pathlib.Path.home() / ".config" / "systemd" / "user"


def _launchd_domain() -> str:
    """Return the current launchd GUI domain."""
    return f"gui/{run_command(['id', '-u']).stdout.strip()}"


def _launchd_extra_env_xml() -> str:
    """Render passthrough launchd env entries for the active shell."""
    lines: list[str] = []
    for key in LAUNCHD_PASSTHROUGH_ENV_VARS:
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        lines.extend((f"      <key>{key}</key>", f"      <string>{escape(value)}</string>"))
    return "\n".join(lines)


def _render_template(
    template_path: pathlib.Path,
    *,
    repo_root: pathlib.Path,
    runtime_layout: RuntimeLayout,
    state_dir: pathlib.Path,
    config_path: pathlib.Path,
) -> str:
    """Render one service template."""
    return (
        load_text(template_path)
        .replace("__REPO_ROOT__", repo_root.as_posix())
        .replace("__PYTHON_EXECUTABLE__", managed_python(repo_root).as_posix())
        .replace("__OPENCLAW_HOME__", runtime_layout.openclaw_home.as_posix())
        .replace("__OPENCLAW_CONFIG_PATH__", config_path.as_posix())
        .replace("__OPENCLAW_CONFIG__", config_path.as_posix())
        .replace("__OPENCLAW_PROFILE__", runtime_layout.openclaw_profile or "")
        .replace(
            "__STRONGCLAW_RUNTIME_ROOT__",
            "" if runtime_layout.runtime_root is None else runtime_layout.runtime_root.as_posix(),
        )
        .replace("__STATE_DIR__", state_dir.as_posix())
        .replace("__HOME_DIR__", pathlib.Path.home().as_posix())
        .replace("__LAUNCHD_EXTRA_ENV__", _launchd_extra_env_xml())
    )


def render_service_files(
    repo_root: pathlib.Path,
    *,
    service_manager: str | None = None,
    state_dir: pathlib.Path | None = None,
) -> dict[str, object]:
    """Render launchd or systemd service definitions for the current host."""
    resolved_repo_root = resolve_repo_root(repo_root)
    layout = resolve_runtime_layout(repo_root=resolved_repo_root)
    resolved_state_dir = (
        state_dir.expanduser().resolve()
        if state_dir is not None
        else resolve_openclaw_state_dir(resolved_repo_root)
    )
    resolved_config_path = resolve_openclaw_config_path(
        resolved_repo_root,
        environ={
            **os.environ,
            "OPENCLAW_STATE_DIR": resolved_state_dir.as_posix(),
        },
    )
    resolved_state_dir.mkdir(parents=True, exist_ok=True)
    (resolved_state_dir / "logs").mkdir(parents=True, exist_ok=True)
    manager = service_manager or resolve_service_manager(detect_host_platform())
    template_paths: list[pathlib.Path]
    if manager == "launchd":
        template_dir = resolved_repo_root / "platform" / "launchd"
        output_dir = launchd_dir()
        template_paths = sorted(template_dir.glob("*.template"))
    elif manager == "systemd":
        template_dir = resolved_repo_root / "platform" / "systemd"
        output_dir = systemd_dir()
        template_paths = sorted([*template_dir.glob("*.service"), *template_dir.glob("*.timer")])
    else:
        raise ValueError(f"unsupported service manager: {manager}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_files: list[str] = []
    for template_path in template_paths:
        if not template_path.is_file():
            continue
        output_name = (
            template_path.stem if template_path.suffix == ".template" else template_path.name
        )
        output_path = output_dir / output_name
        write_text(
            output_path,
            _render_template(
                template_path,
                repo_root=resolved_repo_root,
                runtime_layout=layout,
                state_dir=resolved_state_dir,
                config_path=resolved_config_path,
            ),
        )
        rendered_files.append(output_path.as_posix())
    return {
        "ok": True,
        "serviceManager": manager,
        "outputDir": output_dir.as_posix(),
        "stateDir": resolved_state_dir.as_posix(),
        "renderedFiles": rendered_files,
    }


def _launchd_field(launchctl_output: str, field_name: str) -> str:
    """Return one top-level field from `launchctl print` output."""
    prefix = f"{field_name} = "
    for raw_line in launchctl_output.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def _launchd_timeout_seconds(env_var: str, default: int) -> int:
    """Resolve one positive launchd timeout from the environment."""
    raw_value = os.environ.get(env_var, "").strip()
    if not raw_value:
        return default
    try:
        resolved = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{env_var} must be a positive integer.") from exc
    if resolved < 1:
        raise RuntimeError(f"{env_var} must be a positive integer.")
    return resolved


def _wait_for_launchd_service(label: str, *, persistent: bool, timeout_seconds: int) -> None:
    """Wait for one launchd service to reach its steady state."""
    domain = _launchd_domain()
    deadline = time.monotonic() + timeout_seconds
    last_error_detail = f"launchctl print failed for {label}"
    while True:
        current = run_command(["launchctl", "print", f"{domain}/{label}"], timeout_seconds=15)
        if not current.ok:
            last_error_detail = (
                current.stderr.strip()
                or current.stdout.strip()
                or f"launchctl print failed for {label}"
            )
            if time.monotonic() >= deadline:
                raise RuntimeError(last_error_detail)
            time.sleep(1)
            continue
        state = _launchd_field(current.stdout, "state")
        last_exit_code = _launchd_field(current.stdout, "last exit code")
        if persistent and state == "running":
            return
        if not persistent and last_exit_code == "0":
            return
        if last_exit_code and last_exit_code not in {"0", "(never exited)"}:
            raise RuntimeError(f"{label} exited with code {last_exit_code}")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for {label} to reach its expected launchd state."
            )
        time.sleep(1)


def _activate_launchd_service(domain: str, label: str, plist_path: pathlib.Path) -> None:
    """Bootstrap one launchd agent, replacing an existing instance when present."""
    current = run_command(["launchctl", "print", f"{domain}/{label}"], timeout_seconds=15)
    if current.ok:
        bootout = run_command(["launchctl", "bootout", domain, str(plist_path)], timeout_seconds=30)
        if not bootout.ok:
            detail = bootout.stderr.strip() or bootout.stdout.strip() or "launchctl bootout failed"
            raise RuntimeError(detail)
    bootstrap = run_command(["launchctl", "bootstrap", domain, str(plist_path)], timeout_seconds=30)
    if not bootstrap.ok:
        detail = (
            bootstrap.stderr.strip() or bootstrap.stdout.strip() or "launchctl bootstrap failed"
        )
        raise RuntimeError(detail)


def _activate_launchd_oneshot_service(
    domain: str,
    label: str,
    plist_path: pathlib.Path,
    *,
    timeout_seconds: int,
) -> None:
    """Bootstrap and verify a one-shot launchd agent with one bounded retry."""
    last_error: RuntimeError | None = None
    for attempt in range(1, LAUNCHD_ONESHOT_MAX_ATTEMPTS + 1):
        _activate_launchd_service(domain, label, plist_path)
        try:
            _wait_for_launchd_service(
                label,
                persistent=False,
                timeout_seconds=timeout_seconds,
            )
            return
        except RuntimeError as exc:
            last_error = exc
            if "exited with code" not in str(exc) or attempt >= LAUNCHD_ONESHOT_MAX_ATTEMPTS:
                raise
            time.sleep(LAUNCHD_ONESHOT_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def activate_services(
    repo_root: pathlib.Path,
    *,
    service_manager: str | None = None,
    state_dir: pathlib.Path | None = None,
) -> dict[str, object]:
    """Activate the rendered host service definitions."""
    render_payload = render_service_files(
        repo_root,
        service_manager=service_manager,
        state_dir=state_dir,
    )
    ensure_docker_backend_ready()
    manager = str(render_payload["serviceManager"])
    if manager == "launchd":
        output_dir = pathlib.Path(str(render_payload["outputDir"]))
        domain = _launchd_domain()
        gateway_timeout_seconds = _launchd_timeout_seconds(
            LAUNCHD_GATEWAY_TIMEOUT_ENV_VAR,
            LAUNCHD_GATEWAY_TIMEOUT_SECONDS,
        )
        sidecars_timeout_seconds = _launchd_timeout_seconds(
            LAUNCHD_SIDECARS_TIMEOUT_ENV_VAR,
            LAUNCHD_SIDECARS_TIMEOUT_SECONDS,
        )
        gateway_plist = output_dir / f"{LAUNCHD_GATEWAY_LABEL}.plist"
        sidecars_plist = output_dir / f"{LAUNCHD_SIDECARS_LABEL}.plist"
        backup_create_plist = output_dir / f"{LAUNCHD_BACKUP_CREATE_LABEL}.plist"
        backup_verify_plist = output_dir / f"{LAUNCHD_BACKUP_VERIFY_LABEL}.plist"
        maintenance_plist = output_dir / f"{LAUNCHD_MAINTENANCE_LABEL}.plist"
        emit_structured_log(
            "clawops.services.activate",
            {
                "service_manager": "launchd",
                "step": "sidecars_bootstrap",
                "label": LAUNCHD_SIDECARS_LABEL,
            },
        )
        _activate_launchd_oneshot_service(
            domain,
            LAUNCHD_SIDECARS_LABEL,
            sidecars_plist,
            timeout_seconds=sidecars_timeout_seconds,
        )
        emit_structured_log(
            "clawops.services.activate",
            {
                "service_manager": "launchd",
                "step": "gateway_bootstrap",
                "label": LAUNCHD_GATEWAY_LABEL,
            },
        )
        _activate_launchd_service(domain, LAUNCHD_GATEWAY_LABEL, gateway_plist)
        _wait_for_launchd_service(
            LAUNCHD_GATEWAY_LABEL,
            persistent=True,
            timeout_seconds=gateway_timeout_seconds,
        )
        emit_structured_log(
            "clawops.services.activate",
            {
                "service_manager": "launchd",
                "step": "backup_create_bootstrap",
                "label": LAUNCHD_BACKUP_CREATE_LABEL,
            },
        )
        _activate_launchd_service(
            domain,
            LAUNCHD_BACKUP_CREATE_LABEL,
            backup_create_plist,
        )
        emit_structured_log(
            "clawops.services.activate",
            {
                "service_manager": "launchd",
                "step": "backup_verify_bootstrap",
                "label": LAUNCHD_BACKUP_VERIFY_LABEL,
            },
        )
        _activate_launchd_service(
            domain,
            LAUNCHD_BACKUP_VERIFY_LABEL,
            backup_verify_plist,
        )
        emit_structured_log(
            "clawops.services.activate",
            {
                "service_manager": "launchd",
                "step": "maintenance_bootstrap",
                "label": LAUNCHD_MAINTENANCE_LABEL,
            },
        )
        _activate_launchd_service(domain, LAUNCHD_MAINTENANCE_LABEL, maintenance_plist)
        return {
            **render_payload,
            "activated": list(LAUNCHD_ACTIVATE_LABELS),
        }
    emit_structured_log(
        "clawops.services.activate",
        {
            "service_manager": "systemd",
            "step": "daemon_reload",
        },
    )
    reload_result = run_command(["systemctl", "--user", "daemon-reload"], timeout_seconds=30)
    if not reload_result.ok:
        detail = (
            reload_result.stderr.strip()
            or reload_result.stdout.strip()
            or "systemctl daemon-reload failed"
        )
        raise RuntimeError(detail)
    for unit in SYSTEMD_ACTIVATE_UNITS:
        emit_structured_log(
            "clawops.services.activate",
            {
                "service_manager": "systemd",
                "step": "enable_now",
                "unit": unit,
            },
        )
        enable_result = run_command(
            ["systemctl", "--user", "enable", "--now", unit],
            timeout_seconds=60,
        )
        if not enable_result.ok:
            detail = (
                enable_result.stderr.strip()
                or enable_result.stdout.strip()
                or "systemctl enable failed"
            )
            raise RuntimeError(detail)
    return {
        **render_payload,
        "activated": list(SYSTEMD_ACTIVATE_UNITS),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse arguments for the services CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_asset_root_argument(parser)
    parser.add_argument("--state-dir", type=pathlib.Path, default=None)
    parser.add_argument("--service-manager", choices=("launchd", "systemd"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("render")
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--activate", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for host service rendering and activation."""
    args = parse_args(argv)
    repo_root = resolve_asset_root_argument(args, command_name="clawops services")
    if args.command == "render":
        payload = render_service_files(
            repo_root,
            service_manager=args.service_manager,
            state_dir=args.state_dir,
        )
    elif bool(args.activate):
        payload = activate_services(
            repo_root,
            service_manager=args.service_manager,
            state_dir=args.state_dir,
        )
    else:
        payload = render_service_files(
            repo_root,
            service_manager=args.service_manager,
            state_dir=args.state_dir,
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
