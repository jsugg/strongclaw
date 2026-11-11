"""Real-npm checks that global npm commands cannot break the managed node-tools links."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from clawops.strongclaw_node_tools import (
    LocalPrefixWriter,
    NodeToolsInstaller,
    NodeToolsLayout,
    load_node_tools_bundle,
)
from tests.plugins.infrastructure.context import TestContext

REAL_NPM = shutil.which("npm")
pytestmark = pytest.mark.skipif(REAL_NPM is None, reason="npm is not installed")

_PACKAGES: dict[str, tuple[str, str]] = {
    "openclaw": ("2026.3.13", "openclaw.mjs"),
    "acpx": ("0.3.0", "dist/cli.js"),
}
_FAKE_NPM_CI = """
import json, os, pathlib, sys
if sys.argv[1:3] == ["prefix", "-g"]:
    print(os.environ["npm_config_prefix"])
    raise SystemExit(0)
lock = json.loads(pathlib.Path("package-lock.json").read_text())
for name, version in lock["packages"][""]["dependencies"].items():
    for bin_name, relative in lock["packages"]["node_modules/" + name]["bin"].items():
        target = pathlib.Path("node_modules", name, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"#!/bin/sh\\necho 'managed {name} {version}'\\n")
        target.chmod(0o755)
        link = pathlib.Path("node_modules", ".bin", bin_name)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(target, link.parent))
"""


def _npm(*arguments: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    assert REAL_NPM is not None
    return subprocess.run(
        [REAL_NPM, *arguments, "--offline", "--no-audit", "--no-fund"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _pack_legacy_package(work_dir: pathlib.Path, name: str) -> pathlib.Path:
    """Build an offline tarball shaped like the published package's bin layout."""
    version, relative_bin = _PACKAGES[name]
    package_dir = work_dir / f"src-{name}"
    entry = package_dir / relative_bin
    entry.parent.mkdir(parents=True)
    entry.write_text(f"#!/bin/sh\necho 'legacy {name}'\n", encoding="utf-8")
    entry.chmod(0o755)
    manifest = {"name": name, "version": version, "bin": {name: relative_bin}}
    (package_dir / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    packed = _npm("pack", "--pack-destination", str(work_dir), cwd=package_dir)
    assert packed.returncode == 0, packed.stderr
    return work_dir / f"{name}-{version}.tgz"


def _write_bundle(asset_dir: pathlib.Path) -> None:
    dependencies = {name: version for name, (version, _) in _PACKAGES.items()}
    packages: dict[str, object] = {"": {"name": "t", "dependencies": dependencies}}
    for name, (version, relative_bin) in _PACKAGES.items():
        packages[f"node_modules/{name}"] = {
            "version": version,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
            "integrity": "sha512-" + "A" * 86 + "==",
            "bin": {name: relative_bin},
        }
    asset_dir.mkdir()
    (asset_dir / "package.json").write_text(
        json.dumps({"name": "t", "private": True, "dependencies": dependencies}), encoding="utf-8"
    )
    (asset_dir / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages}), encoding="utf-8"
    )


def _run(path: pathlib.Path) -> str:
    return subprocess.run([str(path)], capture_output=True, text=True, check=True).stdout.strip()


def test_global_npm_commands_cannot_remove_migrated_links(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """After migration, `npm uninstall -g` is a no-op and `npm install -g` refuses to clobber."""
    prefix = tmp_path / "prefix"
    test_context.env.set("npm_config_prefix", str(prefix))
    test_context.env.set("npm_config_cache", str(tmp_path / "npm-cache"))
    tarballs = [_pack_legacy_package(tmp_path, name) for name in _PACKAGES]
    installed = _npm("install", "-g", *map(str, tarballs), cwd=tmp_path)
    assert installed.returncode == 0, installed.stderr
    layout = NodeToolsLayout(prefix)
    assert _run(layout.bin_link("openclaw")) == "legacy openclaw"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "npm").write_text(f"#!{sys.executable}\n{_FAKE_NPM_CI}", encoding="utf-8")
    (fake_bin / "npm").chmod(0o755)
    test_context.env.prepend_path(fake_bin)
    _write_bundle(tmp_path / "assets")
    bundle = load_node_tools_bundle(tmp_path / "assets")
    NodeToolsInstaller(bundle, layout, LocalPrefixWriter()).ensure()

    uninstalled = _npm("uninstall", "-g", *_PACKAGES, cwd=tmp_path)
    reinstalled = _npm("install", "-g", str(tarballs[0]), cwd=tmp_path)

    assert uninstalled.returncode == 0, uninstalled.stderr
    assert reinstalled.returncode != 0
    assert "EEXIST" in reinstalled.stderr or "File exists" in reinstalled.stderr
    for name, (version, _) in _PACKAGES.items():
        link = layout.bin_link(name)
        assert link.is_symlink()
        assert os.readlink(link) == layout.bin_link_target(name)
        assert _run(link) == f"managed {name} {version}"
