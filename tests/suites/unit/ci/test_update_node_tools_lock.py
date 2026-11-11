"""Unit coverage for the node-tools lock update script."""

from __future__ import annotations

import datetime
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from clawops.strongclaw_runtime import CommandError, ExecResult
from tests.plugins.infrastructure.context import TestContext
from tests.scripts import update_node_tools_lock as updater

_INTEGRITY = "sha512-" + "A" * 86 + "=="


def _manifest(pins: dict[str, str]) -> str:
    payload = {"name": "strongclaw-node-tools", "private": True, "dependencies": pins}
    return json.dumps(payload, indent=2) + "\n"


def _fake_generate_lock(work_dir: Path, *, before: str | None) -> None:
    """Write a valid lock for whatever the work-dir manifest pins."""
    del before
    pins: dict[str, str] = json.loads((work_dir / "package.json").read_text())["dependencies"]
    bins = {"openclaw": "openclaw.mjs", "acpx": "dist/cli.js"}
    packages: dict[str, object] = {"": {"name": "strongclaw-node-tools", "dependencies": pins}}
    for name, version in pins.items():
        packages[f"node_modules/{name}"] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
            "integrity": _INTEGRITY,
            "bin": {name: bins[name]},
        }
    lock = {"name": "strongclaw-node-tools", "lockfileVersion": 3, "packages": packages}
    (work_dir / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n")


def _git(repo_root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repo_root), *arguments], check=True, capture_output=True)


def _init_repo(repo_root: Path) -> None:
    """Create a tracked repo with a node-tools bundle, its mirror, and one quoting doc."""
    node_tools_dir = repo_root / "platform" / "node-tools"
    node_tools_dir.mkdir(parents=True)
    (node_tools_dir / "package.json").write_text(
        _manifest({"acpx": "0.3.0", "openclaw": "2026.3.13"})
    )
    _fake_generate_lock(node_tools_dir, before=None)
    (repo_root / "README.md").write_text("Pins OpenClaw `2026.3.13`, ACPX `0.3.0`.\n")
    _git(repo_root, "init", "-q")
    _git(repo_root, "add", ".")
    _git(repo_root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "x")


def _options(repo_root: Path, **overrides: str) -> updater.UpdateOptions:
    return updater.UpdateOptions(
        repo_root=repo_root,
        overrides=overrides,
        min_release_age_days=3,
        refresh=False,
        smoke=False,
        allow_toolchain_mismatch=False,
    )


def test_update_bumps_manifest_lock_mirror_and_quoted_versions(
    test_context: TestContext, tmp_path: Path
) -> None:
    """A bump rewrites the bundle, every quoted version, and the packaged mirror together."""
    repo_root = tmp_path / "repo"
    _init_repo(repo_root)
    seen_before: list[str | None] = []

    def _generate_lock(work_dir: Path, *, before: str | None) -> None:
        seen_before.append(before)
        _fake_generate_lock(work_dir, before=before)

    def _check_toolchain(*, allow_mismatch: bool) -> None:
        del allow_mismatch

    test_context.patch.patch_object(updater, "generate_lock", new=_generate_lock)
    test_context.patch.patch_object(updater, "check_toolchain", new=_check_toolchain)
    now = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.UTC)

    bundle = updater.update_node_tools_lock(_options(repo_root, openclaw="2026.3.14"), now=now)

    assert {package.name: package.version for package in bundle.packages} == {
        "acpx": "0.3.0",
        "openclaw": "2026.3.14",
    }
    assert seen_before == ["2026-11-07T12:00:00Z"]
    assert (repo_root / "README.md").read_text() == "Pins OpenClaw `2026.3.14`, ACPX `0.3.0`.\n"
    mirror = repo_root / "src" / "clawops" / "assets" / "platform" / "node-tools"
    for name in ("package.json", "package-lock.json"):
        assert (mirror / name).read_bytes() == (
            repo_root / "platform" / "node-tools" / name
        ).read_bytes()


def test_release_cutoff_applies_the_quarantine_window() -> None:
    now = datetime.datetime(2026, 11, 10, 9, 30, tzinfo=datetime.UTC)

    assert updater.release_cutoff(0, now=now) is None
    assert updater.release_cutoff(3, now=now) == "2026-11-07T09:30:00Z"


def test_resolve_pins_rejects_ranges(tmp_path: Path) -> None:
    manifest_path = tmp_path / "package.json"
    manifest_path.write_text(_manifest({"acpx": "0.3.0", "openclaw": "2026.3.13"}))

    assert dict(updater.resolve_pins(manifest_path, {"acpx": "0.4.0"})) == {
        "acpx": "0.4.0",
        "openclaw": "2026.3.13",
    }
    with pytest.raises(CommandError, match="exact version"):
        updater.resolve_pins(manifest_path, {"openclaw": "^2026.4.0"})


def test_render_manifest_preserves_metadata_and_sorts_dependencies(tmp_path: Path) -> None:
    manifest_path = tmp_path / "package.json"
    manifest_path.write_text(_manifest({"openclaw": "2026.3.13", "acpx": "0.3.0"}))

    rendered = updater.render_manifest(manifest_path, {"openclaw": "2026.3.14", "acpx": "0.3.0"})

    assert rendered.endswith("}\n")
    assert json.loads(rendered) == {
        "name": "strongclaw-node-tools",
        "private": True,
        "dependencies": {"acpx": "0.3.0", "openclaw": "2026.3.14"},
    }


@pytest.mark.parametrize(("allow_mismatch", "raises"), [(False, True), (True, False)])
def test_check_toolchain_requires_the_ci_node_and_npm(
    test_context: TestContext, allow_mismatch: bool, raises: bool
) -> None:
    versions = {"node": "v24.1.0\n", "npm": "11.0.0\n"}

    def _run_command(command: Sequence[str], *, timeout_seconds: int = 30) -> ExecResult:
        del timeout_seconds
        return ExecResult(
            argv=tuple(command),
            returncode=0,
            stdout=versions[command[0]],
            stderr="",
            duration_ms=1,
        )

    test_context.patch.patch_object(updater, "run_command", new=_run_command)

    if raises:
        with pytest.raises(CommandError, match="22.22.1"):
            updater.check_toolchain(allow_mismatch=allow_mismatch)
    else:
        updater.check_toolchain(allow_mismatch=allow_mismatch)
