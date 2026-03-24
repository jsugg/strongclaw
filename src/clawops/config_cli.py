"""Config management entrypoints for StrongClaw-managed OpenClaw profiles."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import subprocess
from collections.abc import Mapping, Sequence

from clawops.openclaw_config import DEFAULT_OPENCLAW_CONFIG_OUTPUT
from clawops.setup_cli import DEFAULT_REPO_ROOT


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


def _resolve_script(env_name: str, relative_path: str) -> pathlib.Path:
    """Resolve an overrideable shell entrypoint."""
    override = os.environ.get(env_name)
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return (DEFAULT_REPO_ROOT / relative_path).resolve()


def _script_command(script_path: pathlib.Path, argv: Sequence[str] | None) -> list[str]:
    """Build a resilient command line for a local shell entrypoint."""
    args = list(argv or ())
    if script_path.suffix == ".sh":
        return ["/bin/bash", str(script_path), *args]
    return [str(script_path), *args]


def _run_script(script_path: pathlib.Path, argv: Sequence[str] | None) -> None:
    """Execute a local script and raise on failure."""
    if not script_path.exists():
        raise FileNotFoundError(f"missing script: {script_path}")
    result = subprocess.run(_script_command(script_path, argv), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{script_path} exited with code {result.returncode}")


def _memory_profile(profile_id: str) -> MemoryProfileSpec:
    """Resolve one supported StrongClaw memory profile."""
    try:
        return MEMORY_PROFILES[profile_id]
    except KeyError as exc:
        available = ", ".join(sorted(MEMORY_PROFILES))
        raise ValueError(f"unknown memory profile: {profile_id} (choose from {available})") from exc


def _print_payload(payload: Mapping[str, object], *, as_json: bool) -> None:
    """Render a command payload."""
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def _set_memory_profile(
    *,
    profile_id: str,
    output_path: pathlib.Path,
    skip_assets: bool,
) -> dict[str, object]:
    """Install required assets and render the selected OpenClaw profile."""
    profile = _memory_profile(profile_id)
    installed_assets: list[str] = []
    if not skip_assets:
        if profile.installs_qmd:
            _run_script(
                _resolve_script(
                    "CLAWOPS_CONFIG_MEMORY_BOOTSTRAP_QMD_SCRIPT",
                    "scripts/bootstrap/bootstrap_qmd.sh",
                ),
                (),
            )
            installed_assets.append("qmd")
        if profile.installs_lossless_claw:
            _run_script(
                _resolve_script(
                    "CLAWOPS_CONFIG_MEMORY_BOOTSTRAP_LOSSLESS_CLAW_SCRIPT",
                    "scripts/bootstrap/bootstrap_lossless_context_engine.sh",
                ),
                (),
            )
            installed_assets.append("lossless-claw")
        if profile.installs_memory_pro:
            _run_script(
                _resolve_script(
                    "CLAWOPS_CONFIG_MEMORY_BOOTSTRAP_MEMORY_PRO_SCRIPT",
                    "scripts/bootstrap/bootstrap_memory_plugin.sh",
                ),
                (),
            )
            installed_assets.append("memory-lancedb-pro")
    render_script = _resolve_script(
        "CLAWOPS_CONFIG_MEMORY_RENDER_SCRIPT",
        "scripts/bootstrap/render_openclaw_config.sh",
    )
    _run_script(
        render_script,
        (
            "--profile",
            profile.render_profile,
            "--output",
            output_path.expanduser().resolve().as_posix(),
        ),
    )
    return {
        "ok": True,
        "profileId": profile.profile_id,
        "renderProfile": profile.render_profile,
        "output": output_path.expanduser().resolve().as_posix(),
        "installedAssets": installed_assets,
        "description": profile.description,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for StrongClaw config management."""
    parser = argparse.ArgumentParser(description=__doc__)
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
    )
    _print_payload(payload, as_json=bool(args.json))
    return 0
