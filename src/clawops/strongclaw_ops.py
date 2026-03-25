"""Python-native operational commands for gateway and sidecars."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import urllib.error
import urllib.request
from collections.abc import Sequence

from clawops.strongclaw_compose import compose_project_name, resolve_compose_file
from clawops.strongclaw_runtime import (
    DEFAULT_REPO_ROOT,
    CommandError,
    ensure_docker_backend_ready,
    resolve_openclaw_config_path,
    resolve_openclaw_state_dir,
    resolve_repo_local_compose_state_dir,
    resolve_repo_root,
    run_command,
    run_command_inherited,
    wrap_command_with_varlock,
)

DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_TEST_COLLECTION_PREFIX = "memory-v2-int-"


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
    env["OPENCLAW_STATE_DIR"] = str(openclaw_state_dir)
    env["STRONGCLAW_COMPOSE_STATE_DIR"] = str(state_dir)
    env["OPENCLAW_CONFIG"] = str(resolve_openclaw_config_path(repo_root))
    project_name = compose_project_name(
        compose_name=compose_name,
        state_dir=state_dir,
        repo_local_state=repo_local_state,
    )
    if project_name is not None:
        env["COMPOSE_PROJECT_NAME"] = project_name
    return env


def _compose_path(repo_root: pathlib.Path, compose_name: str) -> pathlib.Path:
    """Return one compose file path."""
    return resolve_compose_file(repo_root, compose_name)


def _run_compose_command(
    repo_root: pathlib.Path,
    *,
    compose_name: str,
    arguments: Sequence[str],
    repo_local_state: bool,
    timeout_seconds: int = 1800,
) -> int:
    """Run a docker compose command with StrongClaw state wiring."""
    ensure_docker_backend_ready()
    compose_path = _compose_path(repo_root, compose_name)
    compose_env = _compose_env(
        repo_root,
        repo_local_state=repo_local_state,
        compose_name=compose_name,
    )
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        *[str(argument) for argument in arguments],
    ]
    wrapped = wrap_command_with_varlock(repo_root, command)
    return run_command_inherited(
        wrapped,
        cwd=repo_root / "platform" / "compose",
        env=compose_env,
        timeout_seconds=timeout_seconds,
    )


def gateway_start(repo_root: pathlib.Path) -> int:
    """Run the OpenClaw gateway under Varlock when available."""
    command = wrap_command_with_varlock(repo_root, ["openclaw", "gateway"])
    return run_command_inherited(command, cwd=repo_root, timeout_seconds=1800)


def sidecars_up(repo_root: pathlib.Path, *, repo_local_state: bool) -> int:
    """Start the auxiliary sidecar stack."""
    return _run_compose_command(
        repo_root,
        compose_name="docker-compose.aux-stack.yaml",
        arguments=("up", "-d"),
        repo_local_state=repo_local_state,
    )


def sidecars_down(repo_root: pathlib.Path, *, repo_local_state: bool) -> int:
    """Stop the auxiliary sidecar stack."""
    return _run_compose_command(
        repo_root,
        compose_name="docker-compose.aux-stack.yaml",
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
    compose_result = run_command(
        ["docker", "compose", "-f", str(compose_path), "ps", "--format", "json"],
        cwd=repo_root / "platform" / "compose",
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
    env = _compose_env(
        repo_root,
        repo_local_state=True,
        compose_name=compose_file_name,
    )
    inspect_result = run_command(
        ["docker", "compose", "-f", str(compose_file), "ps", "-q", service_name],
        cwd=repo_root / "platform" / "compose",
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
            cwd=repo_root / "platform" / "compose",
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
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise CommandError(f"failed to query Qdrant collections: {exc}") from exc
    collections = payload.get("result", {}).get("collections", [])
    matching: list[str] = []
    if isinstance(collections, list):
        for entry in collections:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
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
    parser.add_argument("--repo-root", type=pathlib.Path, default=DEFAULT_REPO_ROOT)
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
    repo_root = resolve_repo_root(args.repo_root)
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
