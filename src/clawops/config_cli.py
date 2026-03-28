"""Config management entrypoints for StrongClaw-managed OpenClaw profiles."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
from collections.abc import Mapping

from clawops.common import write_json
from clawops.openclaw_config import (
    DEFAULT_OPENCLAW_CONFIG_OUTPUT,
    materialize_runtime_memory_configs,
    render_openclaw_profile,
)
from clawops.strongclaw_bootstrap import install_profile_assets
from clawops.strongclaw_runtime import resolve_home_dir, resolve_repo_root


@dataclasses.dataclass(frozen=True, slots=True)
class MemoryProfileSpec:
    """StrongClaw-managed OpenClaw memory profile."""

    profile_id: str
    render_profile: str
    description: str
    installs_qmd: bool = False
    installs_lossless_claw: bool = False
    installs_memory_pro: bool = False


MEMORY_PROFILES: dict[str, MemoryProfileSpec] = {
    "hypermemory": MemoryProfileSpec(
        profile_id="hypermemory",
        render_profile="hypermemory",
        description="Default StrongClaw profile: lossless-claw + strongclaw-hypermemory.",
        installs_lossless_claw=True,
    ),
    "openclaw-default": MemoryProfileSpec(
        profile_id="openclaw-default",
        render_profile="openclaw-default",
        description="Built-in OpenClaw defaults: legacy context engine + memory-core.",
    ),
    "openclaw-qmd": MemoryProfileSpec(
        profile_id="openclaw-qmd",
        render_profile="openclaw-qmd",
        description="Built-in OpenClaw defaults plus the experimental QMD memory backend.",
        installs_qmd=True,
    ),
    "memory-lancedb-pro": MemoryProfileSpec(
        profile_id="memory-lancedb-pro",
        render_profile="memory-lancedb-pro",
        description="Vendored memory-lancedb-pro with Ollama-backed smart extraction.",
        installs_qmd=True,
        installs_memory_pro=True,
    ),
}


def _memory_profile(profile_id: str) -> MemoryProfileSpec:
    """Resolve one supported StrongClaw memory profile."""
    try:
        return MEMORY_PROFILES[profile_id]
    except KeyError as exc:
        available = ", ".join(sorted(MEMORY_PROFILES))
        raise ValueError(f"unknown memory profile: {profile_id} (choose from {available})") from exc


def _print_payload(payload: Mapping[str, object], *, as_json: bool) -> None:
    """Render a command payload."""
    del as_json
    print(json.dumps(payload, indent=2, sort_keys=True))


def _set_memory_profile(
    *,
    profile_id: str,
    output_path: pathlib.Path,
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
    resolved_output = output_path.expanduser().resolve()
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
    parser.add_argument("--repo-root", type=pathlib.Path, default=None)
    parser.add_argument("--home-dir", type=pathlib.Path, default=pathlib.Path.home())
    subparsers = parser.add_subparsers(dest="command", required=True)

    memory_parser = subparsers.add_parser("memory", help="Manage StrongClaw memory profiles.")
    memory_parser.add_argument(
        "--set-profile",
        choices=sorted(MEMORY_PROFILES),
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
        help="Target OpenClaw config path. Defaults to ~/.openclaw/openclaw.json.",
    )
    memory_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the StrongClaw config manager."""
    args = parse_args(argv)
    repo_root = resolve_repo_root(args.repo_root)
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
