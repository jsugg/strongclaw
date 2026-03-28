"""Python-native operational commands for gateway and sidecars."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import cast

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.runtime_assets import resolve_asset_path
from clawops.strongclaw_compose import compose_project_name, resolve_compose_file
from clawops.strongclaw_runtime import (
    CommandError,
    ensure_docker_backend_ready,
    load_env_assignments,
    resolve_openclaw_config_path,
    resolve_openclaw_state_dir,
    resolve_repo_local_compose_state_dir,
    run_command,
    run_command_inherited,
    varlock_local_env_file,
    wrap_command_with_varlock,
)
from clawops.typed_values import (
    as_mapping,
    as_mapping_list,
    as_optional_mapping,
    as_optional_string,
    as_string,
)

DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_TEST_COLLECTION_PREFIX = "memory-v2-int-"
SIDECARS_COMPOSE_NAME = "docker-compose.aux-stack.yaml"
POSTGRES_SERVICE_NAME = "postgres"
LITELLM_SERVICE_NAME = "litellm"
SIDECAR_RUNTIME_SERVICE_NAMES = ("litellm", "otel-collector", "qdrant", "neo4j")
COMPOSE_STATUS_TIMEOUT_SECONDS = 30
POSTGRES_HEALTH_TIMEOUT_SECONDS = 180
LITELLM_BOOTSTRAP_TIMEOUT_SECONDS = 1800
COMPOSE_POLL_INTERVAL_SECONDS = 2.0


@dataclasses.dataclass(frozen=True, slots=True)
class _ComposeExecution:
    """Resolved compose execution context."""

    repo_root: pathlib.Path
    compose_path: pathlib.Path
    cwd: pathlib.Path
    env: dict[str, str]

    def command(self, *arguments: str) -> list[str]:
        """Return a wrapped compose command for the configured file."""
        base_command = [
            "docker",
            "compose",
            "-f",
            str(self.compose_path),
            *[str(argument) for argument in arguments],
        ]
        return wrap_command_with_varlock(self.repo_root, base_command)


@dataclasses.dataclass(frozen=True, slots=True)
class _ComposeServiceStatus:
    """Structured service status from `docker compose ps`."""

    name: str
    state: str
    health: str | None = None


def _compose_state_dir(repo_root: pathlib.Path, *, repo_local_state: bool) -> pathlib.Path:
    """Return the effective compose-state directory."""
    if repo_local_state:
        return resolve_repo_local_compose_state_dir(repo_root)
    explicit = os.environ.get("STRONGCLAW_COMPOSE_STATE_DIR", "").strip()
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    return resolve_openclaw_state_dir(repo_root) / "compose"


def _compose_env(
    repo_root: pathlib.Path,
    *,
    repo_local_state: bool,
    compose_name: str,
) -> dict[str, str]:
    """Build the compose execution environment."""
    openclaw_state_dir = resolve_openclaw_state_dir(repo_root)
    state_dir = _compose_state_dir(repo_root, repo_local_state=repo_local_state)
    state_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for key, value in load_env_assignments(varlock_local_env_file(repo_root)).items():
        if value and not env.get(key, "").strip():
            env[key] = value
    env["OPENCLAW_STATE_DIR"] = str(openclaw_state_dir)
    env["STRONGCLAW_COMPOSE_STATE_DIR"] = str(state_dir)
    env["OPENCLAW_CONFIG"] = str(resolve_openclaw_config_path(repo_root))
    project_name = compose_project_name(
        compose_name=compose_name,
        state_dir=state_dir,
        repo_local_state=repo_local_state,
        environ=env,
    )
    if project_name is not None:
        env["COMPOSE_PROJECT_NAME"] = project_name
    return env


def _compose_path(repo_root: pathlib.Path, compose_name: str) -> pathlib.Path:
    """Return one compose file path."""
    return resolve_compose_file(repo_root, compose_name)


def _compose_execution(
    repo_root: pathlib.Path,
    *,
    compose_name: str,
    repo_local_state: bool,
) -> _ComposeExecution:
    """Resolve the compose execution context for one command sequence."""
    ensure_docker_backend_ready()
    compose_path = _compose_path(repo_root, compose_name)
    compose_env = _compose_env(
        repo_root,
        repo_local_state=repo_local_state,
        compose_name=compose_name,
    )
    return _ComposeExecution(
        repo_root=repo_root,
        compose_path=compose_path,
        cwd=resolve_asset_path("platform/compose", repo_root=repo_root),
        env=compose_env,
    )


def _run_compose_command_with_context(
    execution: _ComposeExecution,
    *,
    arguments: Sequence[str],
    timeout_seconds: int = 1800,
) -> int:
    """Run a compose command with inherited stdio for one execution context."""
    return run_command_inherited(
        execution.command(*[str(argument) for argument in arguments]),
        cwd=execution.cwd,
        env=execution.env,
        timeout_seconds=timeout_seconds,
    )


def _run_compose_command(
    repo_root: pathlib.Path,
    *,
    compose_name: str,
    arguments: Sequence[str],
    repo_local_state: bool,
    timeout_seconds: int = 1800,
) -> int:
    """Run a docker compose command with StrongClaw state wiring."""
    execution = _compose_execution(
        repo_root,
        compose_name=compose_name,
        repo_local_state=repo_local_state,
    )
    return _run_compose_command_with_context(
        execution,
        arguments=arguments,
        timeout_seconds=timeout_seconds,
    )


def _compose_service_statuses(execution: _ComposeExecution) -> dict[str, _ComposeServiceStatus]:
    """Return the current compose service states keyed by service name."""
    result = run_command(
        execution.command("ps", "--format", "json"),
        cwd=execution.cwd,
        env=execution.env,
        timeout_seconds=COMPOSE_STATUS_TIMEOUT_SECONDS,
    )
    if not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or "docker compose ps failed"
        raise CommandError(f"failed to inspect sidecar services: {detail}", result=result)
    raw_output = result.stdout.strip()
    if not raw_output:
        return {}

    def _coerce_entries(payload: object) -> tuple[Mapping[str, object], ...]:
        if isinstance(payload, Mapping):
            return (as_mapping(cast(object, payload), path="docker compose ps"),)
        return as_mapping_list(payload, path="docker compose ps")

    try:
        entries = _coerce_entries(json.loads(raw_output))
    except (TypeError, ValueError, json.JSONDecodeError):
        try:
            entries = tuple(
                as_mapping(json.loads(line), path=f"docker compose ps line {index}")
                for index, line in enumerate(raw_output.splitlines())
                if line.strip()
            )
        except (TypeError, ValueError, json.JSONDecodeError) as line_exc:
            raise CommandError("docker compose ps returned invalid JSON.") from line_exc
    statuses: dict[str, _ComposeServiceStatus] = {}
    for index, entry in enumerate(entries):
        service_name = as_string(entry.get("Service"), path=f"docker compose ps[{index}].Service")
        state = as_string(entry.get("State"), path=f"docker compose ps[{index}].State").strip()
        health = as_optional_string(
            entry.get("Health"),
            path=f"docker compose ps[{index}].Health",
        )
        normalized_health = None if health is None else health.strip().lower() or None
        statuses[service_name] = _ComposeServiceStatus(
            name=service_name,
            state=state.lower(),
            health=normalized_health,
        )
    return statuses


def _service_matches(
    status: _ComposeServiceStatus | None,
    *,
    state: str,
    health: str | None = None,
) -> bool:
    """Return whether one compose service matches the required state."""
    if status is None or status.state != state:
        return False
    if health is None:
        return True
    return status.health == health


def _wait_for_compose_service(
    execution: _ComposeExecution,
    *,
    service_name: str,
    state: str,
    health: str | None = None,
    timeout_seconds: int,
) -> None:
    """Wait for one compose service to reach the requested state."""
    deadline = time.monotonic() + timeout_seconds
    last_status: _ComposeServiceStatus | None = None
    while True:
        last_status = _compose_service_statuses(execution).get(service_name)
        if _service_matches(last_status, state=state, health=health):
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(COMPOSE_POLL_INTERVAL_SECONDS)
    target = state if health is None else f"{state}/{health}"
    observed = (
        "service not listed in compose status"
        if last_status is None
        else f"state={last_status.state!r}, health={last_status.health or 'n/a'!r}"
    )
    raise CommandError(
        f"timed out waiting for compose service '{service_name}' to reach {target}; "
        f"last observed {observed}."
    )


def _run_litellm_schema_bootstrap(execution: _ComposeExecution) -> int:
    """Run LiteLLM schema bootstrap without treating it as a long-lived service."""
    return _run_compose_command_with_context(
        execution,
        arguments=(
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "DISABLE_SCHEMA_UPDATE=false",
            LITELLM_SERVICE_NAME,
            "--config",
            "/app/config.yaml",
            "--skip_server_startup",
        ),
        timeout_seconds=LITELLM_BOOTSTRAP_TIMEOUT_SECONDS,
    )


def gateway_start(repo_root: pathlib.Path) -> int:
    """Run the OpenClaw gateway under Varlock when available."""
    command = wrap_command_with_varlock(repo_root, ["openclaw", "gateway"])
    return run_command_inherited(command, cwd=repo_root, timeout_seconds=1800)


def sidecars_up(repo_root: pathlib.Path, *, repo_local_state: bool) -> int:
    """Start the auxiliary sidecar stack."""
    execution = _compose_execution(
        repo_root,
        compose_name=SIDECARS_COMPOSE_NAME,
        repo_local_state=repo_local_state,
    )
    postgres_exit = _run_compose_command_with_context(
        execution,
        arguments=("up", "-d", POSTGRES_SERVICE_NAME),
    )
    if postgres_exit != 0:
        return postgres_exit
    _wait_for_compose_service(
        execution,
        service_name=POSTGRES_SERVICE_NAME,
        state="running",
        health="healthy",
        timeout_seconds=POSTGRES_HEALTH_TIMEOUT_SECONDS,
    )
    litellm_status = _compose_service_statuses(execution).get(LITELLM_SERVICE_NAME)
    litellm_ready = _service_matches(litellm_status, state="running", health="healthy")
    if not litellm_ready:
        bootstrap_exit = _run_litellm_schema_bootstrap(execution)
        if bootstrap_exit != 0:
            return bootstrap_exit
        litellm_exit = _run_compose_command_with_context(
            execution,
            arguments=("up", "-d", "--force-recreate", LITELLM_SERVICE_NAME),
        )
        if litellm_exit != 0:
            return litellm_exit
        runtime_services = tuple(
            service_name
            for service_name in SIDECAR_RUNTIME_SERVICE_NAMES
            if service_name != LITELLM_SERVICE_NAME
        )
        return _run_compose_command_with_context(
            execution,
            arguments=("up", "-d", *runtime_services),
        )
    return _run_compose_command_with_context(
        execution,
        arguments=("up", "-d", *SIDECAR_RUNTIME_SERVICE_NAMES),
    )


def sidecars_down(repo_root: pathlib.Path, *, repo_local_state: bool) -> int:
    """Stop the auxiliary sidecar stack."""
    return _run_compose_command(
        repo_root,
        compose_name=SIDECARS_COMPOSE_NAME,
        arguments=("down",),
        repo_local_state=repo_local_state,
    )


def browser_lab_up(repo_root: pathlib.Path, *, repo_local_state: bool) -> int:
    """Start the browser-lab stack."""
    return _run_compose_command(
        repo_root,
        compose_name="docker-compose.browser-lab.yaml",
        arguments=("up", "-d"),
        repo_local_state=repo_local_state,
    )


def browser_lab_down(repo_root: pathlib.Path, *, repo_local_state: bool) -> int:
    """Stop the browser-lab stack."""
    return _run_compose_command(
        repo_root,
        compose_name="docker-compose.browser-lab.yaml",
        arguments=("down",),
        repo_local_state=repo_local_state,
    )


def status(repo_root: pathlib.Path, *, repo_local_state: bool) -> dict[str, object]:
    """Return the current sidecar compose status."""
    compose_name = "docker-compose.aux-stack.yaml"
    env = _compose_env(repo_root, repo_local_state=repo_local_state, compose_name=compose_name)
    compose_path = _compose_path(repo_root, compose_name)
    compose_cwd = resolve_asset_path("platform/compose", repo_root=repo_root)
    compose_result = run_command(
        ["docker", "compose", "-f", str(compose_path), "ps", "--format", "json"],
        cwd=compose_cwd,
        env=env,
        timeout_seconds=30,
    )
    return {
        "ok": compose_result.ok,
        "composeStateDir": env["STRONGCLAW_COMPOSE_STATE_DIR"],
        "openclawConfig": env["OPENCLAW_CONFIG"],
        "compose": (
            compose_result.stdout.strip() if compose_result.ok else compose_result.stderr.strip()
        ),
    }


def reset_compose_state(
    repo_root: pathlib.Path,
    *,
    component: str,
    state_dir: pathlib.Path | None = None,
    force_stop: bool,
) -> dict[str, object]:
    """Reset one compose-state component directory."""
    component_map = {
        "postgres": ("docker-compose.aux-stack.yaml", "postgres", "postgres"),
        "qdrant": ("docker-compose.aux-stack.yaml", "qdrant", "qdrant"),
        "litellm": ("docker-compose.aux-stack.yaml", "litellm", "litellm"),
        "otel": ("docker-compose.aux-stack.yaml", "otel-collector", "otel"),
        "browser-lab": ("docker-compose.browser-lab.yaml", "browserlab-playwright", "browser-lab"),
    }
    try:
        compose_file_name, service_name, component_dir = component_map[component]
    except KeyError as exc:
        raise CommandError(f"unsupported component: {component}") from exc
    target_root = (
        state_dir.expanduser().resolve()
        if state_dir is not None
        else resolve_repo_local_compose_state_dir(repo_root)
    )
    target_dir = target_root / component_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    compose_file = _compose_path(repo_root, compose_file_name)
    compose_cwd = resolve_asset_path("platform/compose", repo_root=repo_root)
    env = _compose_env(
        repo_root,
        repo_local_state=True,
        compose_name=compose_file_name,
    )
    inspect_result = run_command(
        ["docker", "compose", "-f", str(compose_file), "ps", "-q", service_name],
        cwd=compose_cwd,
        env=env,
        timeout_seconds=30,
    )
    container_id = inspect_result.stdout.strip()
    if container_id and not force_stop:
        raise CommandError(
            f"{component} is still running. Stop it first or rerun with --force-stop."
        )
    if container_id:
        stop_result = run_command(
            ["docker", "compose", "-f", str(compose_file), "stop", service_name],
            cwd=compose_cwd,
            env=env,
            timeout_seconds=120,
        )
        if not stop_result.ok:
            detail = (
                stop_result.stderr.strip() or stop_result.stdout.strip() or "docker stop failed"
            )
            raise CommandError(detail, result=stop_result)
    for child in target_dir.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink()
    return {"ok": True, "component": component, "stateDir": str(target_dir)}


def prune_qdrant_test_collections(
    *,
    qdrant_url: str,
    prefixes: Sequence[str],
    dry_run: bool,
) -> dict[str, object]:
    """Delete stale Qdrant test collections."""
    request = urllib.request.Request(f"{qdrant_url.rstrip('/')}/collections", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = as_mapping(
                json.loads(response.read().decode("utf-8")),
                path="qdrant collections response",
            )
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise CommandError(f"failed to query Qdrant collections: {exc}") from exc
    result = as_optional_mapping(payload.get("result"), path="qdrant collections response.result")
    collections_value: object = [] if result is None else result.get("collections", [])
    matching: list[str] = []
    if isinstance(collections_value, list):
        for entry in cast(Sequence[object], collections_value):
            if not isinstance(entry, Mapping):
                continue
            entry_mapping_any = cast(Mapping[object, object], entry)
            if any(not isinstance(key, str) for key in entry_mapping_any):
                continue
            entry_mapping = cast(Mapping[str, object], entry)
            name = entry_mapping.get("name")
            if isinstance(name, str) and any(name.startswith(prefix) for prefix in prefixes):
                matching.append(name)
    pruned: list[str] = []
    for collection in matching:
        if dry_run:
            pruned.append(collection)
            continue
        delete_request = urllib.request.Request(
            f"{qdrant_url.rstrip('/')}/collections/{collection}",
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(delete_request, timeout=15):
                pass
        except OSError as exc:
            raise CommandError(f"failed to prune {collection}: {exc}") from exc
        pruned.append(collection)
    return {"ok": True, "dryRun": dry_run, "collections": pruned}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for operational commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_asset_root_argument(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    gateway = subparsers.add_parser("gateway")
    gateway_sub = gateway.add_subparsers(dest="gateway_command", required=True)
    gateway_sub.add_parser("start")

    sidecars = subparsers.add_parser("sidecars")
    sidecars_sub = sidecars.add_subparsers(dest="sidecars_command", required=True)
    sidecars_up_parser = sidecars_sub.add_parser("up")
    sidecars_up_parser.add_argument("--repo-local-state", action="store_true")
    sidecars_down_parser = sidecars_sub.add_parser("down")
    sidecars_down_parser.add_argument("--repo-local-state", action="store_true")

    browser_lab = subparsers.add_parser("browser-lab")
    browser_lab_sub = browser_lab.add_subparsers(dest="browser_lab_command", required=True)
    browser_lab_up_parser = browser_lab_sub.add_parser("up")
    browser_lab_up_parser.add_argument("--repo-local-state", action="store_true")
    browser_lab_down_parser = browser_lab_sub.add_parser("down")
    browser_lab_down_parser.add_argument("--repo-local-state", action="store_true")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--repo-local-state", action="store_true")

    reset_parser = subparsers.add_parser("reset-compose-state")
    reset_parser.add_argument(
        "--component",
        required=True,
        choices=("postgres", "qdrant", "litellm", "otel", "browser-lab"),
    )
    reset_parser.add_argument("--state-dir", type=pathlib.Path, default=None)
    reset_parser.add_argument("--force-stop", action="store_true")

    prune_parser = subparsers.add_parser("prune-qdrant-test-collections")
    prune_parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    prune_parser.add_argument("--prefix", action="append", default=[DEFAULT_TEST_COLLECTION_PREFIX])
    prune_parser.add_argument("--dry-run", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for StrongClaw operational commands."""
    args = parse_args(argv)
    repo_root = resolve_asset_root_argument(args, command_name="clawops ops")
    if args.command == "gateway":
        return gateway_start(repo_root)
    if args.command == "sidecars":
        if args.sidecars_command == "up":
            return sidecars_up(repo_root, repo_local_state=bool(args.repo_local_state))
        return sidecars_down(repo_root, repo_local_state=bool(args.repo_local_state))
    if args.command == "browser-lab":
        if args.browser_lab_command == "up":
            return browser_lab_up(repo_root, repo_local_state=bool(args.repo_local_state))
        return browser_lab_down(repo_root, repo_local_state=bool(args.repo_local_state))
    if args.command == "status":
        print(
            json.dumps(
                status(repo_root, repo_local_state=bool(args.repo_local_state)), sort_keys=True
            )
        )
        return 0
    if args.command == "reset-compose-state":
        payload = reset_compose_state(
            repo_root,
            component=str(args.component),
            state_dir=args.state_dir,
            force_stop=bool(args.force_stop),
        )
        print(json.dumps(payload, sort_keys=True))
        return 0
    payload = prune_qdrant_test_collections(
        qdrant_url=str(args.qdrant_url),
        prefixes=tuple(str(prefix) for prefix in args.prefix),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(payload, sort_keys=True))
    return 0
