"""Real-npm checks for the managed node-tools layout and its legacy compat links."""

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
    NodeToolsBundle,
    NodeToolsInstaller,
    NodeToolsInstallResult,
    NodeToolsLayout,
    load_node_tools_bundle,
)
from tests.plugins.infrastructure.context import TestContext

REAL_NPM = shutil.which("npm")
REAL_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    REAL_NPM is None or REAL_NODE is None, reason="npm and node are required"
)

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
    package_dir = pathlib.Path("node_modules", name)
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "type": "module"}
    manifest["bin"] = lock["packages"]["node_modules/" + name]["bin"]
    (package_dir / "package.json").write_text(json.dumps(manifest))
    api = package_dir / "dist" / "extensionAPI.js"
    api.parent.mkdir(parents=True, exist_ok=True)
    api.write_text(f"export async function runEmbeddedPiAgent() {{ return 'managed {version}'; }}\\n")
    for bin_name, relative in manifest["bin"].items():
        target = package_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"#!/bin/sh\\necho 'managed {name} {version}'\\n")
        target.chmod(0o755)
        link = pathlib.Path("node_modules", ".bin", bin_name)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(target, link.parent))
"""
_IMPORT_RUNNER = (
    "const m = await import(process.argv[1]);" "console.log(await m.runEmbeddedPiAgent());"
)


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


def _write_bundle(asset_dir: pathlib.Path) -> NodeToolsBundle:
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
    return load_node_tools_bundle(asset_dir)


def _run(path: pathlib.Path) -> str:
    return subprocess.run([str(path)], capture_output=True, text=True, check=True).stdout.strip()


def _assert_managed(layout: NodeToolsLayout) -> None:
    for name, (version, _) in _PACKAGES.items():
        assert os.readlink(layout.bin_link(name)) == layout.bin_link_target(name)
        assert _run(layout.bin_link(name)) == f"managed {name} {version}"
        assert os.readlink(layout.compat_package_link(name)) == layout.compat_package_target(name)


@pytest.fixture
def legacy_prefix(test_context: TestContext, tmp_path: pathlib.Path) -> NodeToolsLayout:
    """Return a prefix holding a real `npm install -g` of both packages, plus a fake `npm ci`."""
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
    return layout


def _ensure(layout: NodeToolsLayout, bundle: NodeToolsBundle) -> NodeToolsInstallResult:
    return NodeToolsInstaller(bundle, layout, LocalPrefixWriter()).ensure()


def test_compat_links_serve_legacy_absolute_paths_and_npm_ls(
    legacy_prefix: NodeToolsLayout, tmp_path: pathlib.Path
) -> None:
    """The legacy `extensionAPI.js` path imports the managed release; npm lists the links."""
    bundle = _write_bundle(tmp_path / "assets")
    _ensure(legacy_prefix, bundle)

    _assert_managed(legacy_prefix)
    legacy_api = legacy_prefix.compat_package_link("openclaw") / "dist" / "extensionAPI.js"
    assert REAL_NODE is not None
    imported = subprocess.run(
        [REAL_NODE, "--input-type=module", "-e", _IMPORT_RUNNER, legacy_api.as_uri()],
        capture_output=True,
        text=True,
        check=True,
    )
    assert imported.stdout.strip() == "managed 2026.3.13"
    listed = _npm("ls", "-g", "--depth=0", cwd=tmp_path)
    assert listed.returncode == 0, listed.stderr
    for name, (version, _) in _PACKAGES.items():
        assert f"{name}@{version} -> " in listed.stdout


def test_npm_uninstall_only_unlinks_and_bootstrap_restores(
    legacy_prefix: NodeToolsLayout, tmp_path: pathlib.Path
) -> None:
    """`npm uninstall -g` removes one package's links, never the release; a re-run restores."""
    bundle = _write_bundle(tmp_path / "assets")
    _ensure(legacy_prefix, bundle)
    release_dir = legacy_prefix.release_dir(bundle.digest)

    uninstalled = _npm("uninstall", "-g", "openclaw", cwd=tmp_path)

    assert uninstalled.returncode == 0, uninstalled.stderr
    assert not legacy_prefix.bin_link("openclaw").is_symlink()
    assert not legacy_prefix.compat_package_link("openclaw").is_symlink()
    assert (release_dir / "node_modules" / "openclaw" / "openclaw.mjs").is_file()
    assert _run(legacy_prefix.bin_link("acpx")) == "managed acpx 0.3.0"

    restored = _ensure(legacy_prefix, bundle)

    assert restored.changed is True
    _assert_managed(legacy_prefix)


def test_npm_install_over_a_compat_link_is_reconciled_by_bootstrap(
    legacy_prefix: NodeToolsLayout, tmp_path: pathlib.Path
) -> None:
    """A user `npm install -g` override is migrated back to the locked tree on the next run."""
    bundle = _write_bundle(tmp_path / "assets")
    _ensure(legacy_prefix, bundle)

    reinstalled = _npm("install", "-g", str(tmp_path / "openclaw-2026.3.13.tgz"), cwd=tmp_path)

    assert reinstalled.returncode == 0, reinstalled.stderr
    assert _run(legacy_prefix.bin_link("openclaw")) == "legacy openclaw"

    _ensure(legacy_prefix, bundle)

    _assert_managed(legacy_prefix)
