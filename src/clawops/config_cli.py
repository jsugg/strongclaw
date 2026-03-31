"""Config management entrypoints for StrongClaw-managed OpenClaw profiles."""

from __future__ import annotations

import argparse
import json
import pathlib
from collections.abc import Mapping

from clawops.cli_roots import add_asset_root_argument, resolve_asset_root_argument
from clawops.common import write_json
from clawops.memory_profiles import (
    MANAGED_MEMORY_PROFILE_IDS,
    MEMORY_PROFILES,
    MemoryProfileSpec,
    require_memory_profile,
)
from clawops.openclaw_config import (
    DEFAULT_OPENCLAW_CONFIG_OUTPUT,
    materialize_runtime_memory_configs,
    render_openclaw_profile,
)
from clawops.runtime_assets import resolve_runtime_layout
from clawops.strongclaw_bootstrap import install_profile_assets
from clawops.strongclaw_runtime import resolve_home_dir


def _memory_profile(profile_id: str) -> MemoryProfileSpec:
    """Resolve one supported StrongClaw memory profile."""
    return require_memory_profile(profile_id)


def _print_payload(payload: Mapping[str, object], *, as_json: bool) -> None:
    """Render a command payload."""
    del as_json
    print(json.dumps(payload, indent=2, sort_keys=True))


def _set_memory_profile(
    *,
    profile_id: str,
    output_path: pathlib.Path | None,
    skip_assets: bool,
    repo_root: pathlib.Path,
    home_dir: pathlib.Path,
) -> dict[str, object]:
    """Install required assets and render the selected OpenClaw profile."""
    profile = _memory_profile(profile_id)
    installed_assets: list[str] = []
    if not skip_assets:
        installed_assets = install_profile_assets(
            repo_root,
            profile=profile.render_profile,
            home_dir=home_dir,
        )
    rendered = render_openclaw_profile(
        profile_name=profile.render_profile,
        repo_root=repo_root,
        home_dir=home_dir,
    )
    materialize_runtime_memory_configs(repo_root=repo_root, home_dir=home_dir)
    layout = resolve_runtime_layout(repo_root=repo_root, home_dir=home_dir)
    resolved_output = (
        layout.openclaw_config_path if output_path is None else output_path.expanduser().resolve()
    )
    write_json(resolved_output, rendered)
    return {
        "ok": True,
        "profileId": profile.profile_id,
        "renderProfile": profile.render_profile,
        "output": resolved_output.as_posix(),
        "installedAssets": installed_assets,
        "description": profile.description,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for StrongClaw config management."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_asset_root_argument(parser)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    subparsers = parser.add_subparsers(dest="command", required=True)

    memory_parser = subparsers.add_parser("memory", help="Manage StrongClaw memory profiles.")
    memory_parser.add_argument(
        "--set-profile",
        choices=sorted(MANAGED_MEMORY_PROFILE_IDS),
        help="Install assets as needed and render the selected memory profile.",
    )
    memory_parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List the supported StrongClaw memory profile ids.",
    )
    memory_parser.add_argument(
        "--skip-assets",
        action="store_true",
        help="Only rerender config; do not install or refresh profile assets.",
    )
    memory_parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=DEFAULT_OPENCLAW_CONFIG_OUTPUT,
        help="Target OpenClaw config path. Defaults to the active runtime boundary.",
    )
    memory_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the StrongClaw config manager."""
    args = parse_args(argv)
    repo_root = resolve_asset_root_argument(args, command_name="clawops config")
    home_dir = resolve_home_dir(args.home_dir)
    if args.command != "memory":
        raise SystemExit(f"unsupported config command: {args.command}")
    if bool(args.list_profiles):
        list_payload: dict[str, object] = {
            "profiles": [
                {
                    "id": profile.profile_id,
                    "renderProfile": profile.render_profile,
                    "description": profile.description,
                }
                for profile in MEMORY_PROFILES.values()
                if profile.managed
            ]
        }
        _print_payload(list_payload, as_json=bool(args.json))
        return 0
    if args.set_profile is None:
        raise SystemExit("memory config requires --set-profile or --list-profiles")
    payload = _set_memory_profile(
        profile_id=str(args.set_profile),
        output_path=args.output,
        skip_assets=bool(args.skip_assets),
        repo_root=repo_root,
        home_dir=home_dir,
    )
    _print_payload(payload, as_json=bool(args.json))
    return 0
