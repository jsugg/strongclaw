#!/usr/bin/env python3
"""Bump the locked OpenClaw/ACPX node-tools tree and every surface that quotes its versions.

The lock is regenerated with the CI toolchain (Node 22.22.1 / npm 10.9.4), restricted to
releases older than a quarantine window, validated, optionally smoke-installed, and then
mirrored into the packaged assets alongside the docs and env templates that quote the pins.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from clawops.openclaw_plugins import FIRST_PARTY_PLUGIN_SCOPE, FIRST_PARTY_PLUGINS  # noqa: E402
from clawops.platform_compat import parse_node_tool_pins  # noqa: E402
from clawops.strongclaw_node_tools import (  # noqa: E402
    LOCK_FILE_NAME,
    MANIFEST_FILE_NAME,
    NODE_TOOLS_ASSET_DIR,
    LocalPrefixWriter,
    NodeToolsBundle,
    load_node_tools_bundle,
    smoke_test_node_tools,
    stage_node_tools_tree,
)
from clawops.strongclaw_runtime import (  # noqa: E402
    CommandError,
    run_command,
    run_command_inherited,
)
from tests.scripts.sync_runtime_assets import sync_runtime_assets  # noqa: E402
from tests.utils.helpers.node_tools import rewrite_version_mentions  # noqa: E402

REQUIRED_NODE_VERSION: Final[str] = "22.22.1"
REQUIRED_NPM_VERSION: Final[str] = "10.9.4"
DEFAULT_MIN_RELEASE_AGE_DAYS: Final[int] = 3
_NPM_LOCK_TIMEOUT_SECONDS: Final[int] = 1800


@dataclasses.dataclass(frozen=True, slots=True)
class UpdateOptions:
    """Validated command-line options."""

    repo_root: Path
    overrides: Mapping[str, str]
    min_release_age_days: int
    refresh: bool
    smoke: bool
    allow_toolchain_mismatch: bool


def _parse_args(argv: Sequence[str] | None = None) -> UpdateOptions:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--openclaw", help="Exact OpenClaw version to pin.")
    parser.add_argument("--acpx", help="Exact ACPX version to pin.")
    parser.add_argument(
        "--min-release-age-days",
        type=int,
        default=DEFAULT_MIN_RELEASE_AGE_DAYS,
        help="Only resolve releases at least this old (npm --before); 0 disables the window.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Discard the existing lock and re-resolve the whole transitive tree.",
    )
    parser.add_argument("--skip-smoke", action="store_true", help="Skip the npm ci smoke test.")
    parser.add_argument(
        "--allow-toolchain-mismatch",
        action="store_true",
        help=f"Permit a toolchain other than Node {REQUIRED_NODE_VERSION}/npm {REQUIRED_NPM_VERSION}.",
    )
    args = parser.parse_args(argv)
    min_release_age_days = cast(int, args.min_release_age_days)
    if min_release_age_days < 0:
        parser.error("--min-release-age-days must be >= 0")
    overrides = {
        name: version
        for name, version in (("openclaw", args.openclaw), ("acpx", args.acpx))
        if isinstance(version, str)
    }
    return UpdateOptions(
        repo_root=cast(Path, args.repo_root).expanduser().resolve(),
        overrides=overrides,
        min_release_age_days=min_release_age_days,
        refresh=cast(bool, args.refresh),
        smoke=not cast(bool, args.skip_smoke),
        allow_toolchain_mismatch=cast(bool, args.allow_toolchain_mismatch),
    )


def _tool_version(command: str) -> str:
    """Return the `--version` output of one toolchain command."""
    result = run_command([command, "--version"], timeout_seconds=30)
    if not result.ok:
        raise CommandError(f"{command} --version failed", result=result)
    return result.stdout.strip().removeprefix("v")


def check_toolchain(*, allow_mismatch: bool) -> None:
    """Require the Node/npm toolchain CI uses so the lock format and resolution match."""
    found = {"node": _tool_version("node"), "npm": _tool_version("npm")}
    expected = {"node": REQUIRED_NODE_VERSION, "npm": REQUIRED_NPM_VERSION}
    if found == expected:
        return
    message = f"lock generation expects {expected}, found {found}"
    if not allow_mismatch:
        raise CommandError(f"{message}; pass --allow-toolchain-mismatch to override")
    print(f"warning: {message}", file=sys.stderr)


def release_cutoff(min_release_age_days: int, *, now: datetime.datetime) -> str | None:
    """Return the npm `--before` timestamp for the quarantine window, if enabled."""
    if min_release_age_days == 0:
        return None
    cutoff = now - datetime.timedelta(days=min_release_age_days)
    return cutoff.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_manifest(manifest_path: Path, pins: Mapping[str, str]) -> str:
    """Return the manifest text with its dependencies replaced by *pins*."""
    payload = cast(object, json.loads(manifest_path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise CommandError(f"{manifest_path}: manifest must be a JSON object")
    manifest = cast(dict[str, object], payload)
    manifest["dependencies"] = {name: pins[name] for name in sorted(pins)}
    return json.dumps(manifest, indent=2) + "\n"


def resolve_pins(manifest_path: Path, overrides: Mapping[str, str]) -> Mapping[str, str]:
    """Merge version overrides into the current pins and validate the result.

    First-party OpenClaw plugins come from the plugin registry and always track the OpenClaw
    pin, since each release peers on its own host version.
    """
    current = parse_node_tool_pins(
        json.loads(manifest_path.read_text(encoding="utf-8")), source=str(manifest_path)
    )
    merged = {
        name: version
        for name, version in {**current, **overrides}.items()
        if not name.startswith(FIRST_PARTY_PLUGIN_SCOPE)
    }
    merged.update({plugin.package: merged["openclaw"] for plugin in FIRST_PARTY_PLUGINS})
    try:
        return parse_node_tool_pins({"dependencies": merged}, source="requested pins")
    except ValueError as err:
        raise CommandError(str(err)) from err


def generate_lock(work_dir: Path, *, before: str | None) -> None:
    """Resolve `package-lock.json` in *work_dir* without installing anything."""
    command = [
        "npm",
        "install",
        "--package-lock-only",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ]
    if before is not None:
        command.append(f"--before={before}")
    returncode = run_command_inherited(
        command, cwd=work_dir, timeout_seconds=_NPM_LOCK_TIMEOUT_SECONDS
    )
    if returncode != 0:
        raise CommandError(f"npm lock generation failed with exit code {returncode}")


def smoke_install(bundle: NodeToolsBundle, work_dir: Path) -> None:
    """Install the generated lock with `npm ci` and run the bootstrap smoke test."""
    install_dir = work_dir / "smoke"
    stage_node_tools_tree(bundle, install_dir, LocalPrefixWriter())
    smoke_test_node_tools(install_dir / "node_modules" / ".bin", bundle)


def update_node_tools_lock(
    options: UpdateOptions, *, now: datetime.datetime | None = None
) -> NodeToolsBundle:
    """Regenerate, validate, and publish the node-tools lock plus dependent surfaces."""
    source_dir = options.repo_root / NODE_TOOLS_ASSET_DIR
    manifest_path = source_dir / MANIFEST_FILE_NAME
    lock_path = source_dir / LOCK_FILE_NAME
    pins = resolve_pins(manifest_path, options.overrides)
    check_toolchain(allow_mismatch=options.allow_toolchain_mismatch)
    before = release_cutoff(
        options.min_release_age_days, now=now or datetime.datetime.now(datetime.UTC)
    )
    with tempfile.TemporaryDirectory(prefix="strongclaw-node-tools-lock.") as temp_dir:
        work_dir = Path(temp_dir)
        (work_dir / MANIFEST_FILE_NAME).write_text(
            render_manifest(manifest_path, pins), encoding="utf-8"
        )
        if not options.refresh and lock_path.is_file():
            shutil.copyfile(lock_path, work_dir / LOCK_FILE_NAME)
        generate_lock(work_dir, before=before)
        bundle = load_node_tools_bundle(work_dir)
        if options.smoke:
            smoke_install(bundle, work_dir)
        shutil.copyfile(work_dir / MANIFEST_FILE_NAME, manifest_path)
        shutil.copyfile(work_dir / LOCK_FILE_NAME, lock_path)
    changed = rewrite_version_mentions(options.repo_root, pins)
    copied, removed = sync_runtime_assets(options.repo_root)
    print(
        "updated node-tools lock: "
        + ", ".join(f"{name}@{version}" for name, version in sorted(pins.items()))
        + f"; rewrote {len(changed)} version surfaces; mirror copied={copied} removed={removed}"
    )
    return load_node_tools_bundle(source_dir)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the node-tools lock update."""
    options = _parse_args(argv)
    try:
        update_node_tools_lock(options)
    except CommandError as err:
        print(f"update-node-tools-lock error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
