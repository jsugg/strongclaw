"""Contracts for the committed OpenClaw/ACPX node-tools manifest and lock."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import cast

import pytest

from clawops import platform_compat
from clawops.strongclaw_node_tools import NODE_TOOLS_ASSET_DIR, load_node_tools_bundle
from tests.utils.helpers.ci_node_installs import (
    find_floating_node_commands,
    find_floating_node_installs,
)
from tests.utils.helpers.node_tools import find_version_mentions
from tests.utils.helpers.repo import REPO_ROOT

_SOURCE_DIR = REPO_ROOT / NODE_TOOLS_ASSET_DIR
_PACKAGED_DIR = REPO_ROOT / "src" / "clawops" / "assets" / NODE_TOOLS_ASSET_DIR
# npm cannot record integrity for git dependencies; each one must be reviewed explicitly.
_REVIEWED_GIT_DEPENDENCIES = frozenset({"node_modules/libsignal"})


def _lock_packages() -> dict[str, dict[str, object]]:
    payload = json.loads((_SOURCE_DIR / "package-lock.json").read_text(encoding="utf-8"))
    return cast(dict[str, dict[str, object]], payload["packages"])


@pytest.mark.parametrize("asset_dir", [_SOURCE_DIR, _PACKAGED_DIR], ids=["source", "packaged"])
def test_node_tools_bundle_is_valid_and_exposes_both_clis(asset_dir: Path) -> None:
    """Both copies validate, agree with the manifest pins, and expose the expected bins."""
    bundle = load_node_tools_bundle(asset_dir)

    assert bundle.digest == load_node_tools_bundle(_SOURCE_DIR).digest
    assert {package.name: package.version for package in bundle.packages} == dict(
        platform_compat.NODE_TOOL_PINS
    )
    assert {package.name: package.bins for package in bundle.packages} == {
        "openclaw": ("openclaw",),
        "acpx": ("acpx",),
    }


def test_python_version_constants_derive_from_the_manifest() -> None:
    """The manifest is the single source of truth for the Python-visible versions."""
    manifest = json.loads((_SOURCE_DIR / "package.json").read_text(encoding="utf-8"))

    assert platform_compat.NODE_TOOLS_MANIFEST_PATH == _PACKAGED_DIR / "package.json"
    assert dict(platform_compat.NODE_TOOL_PINS) == manifest["dependencies"]
    assert platform_compat.DEFAULT_OPENCLAW_VERSION == manifest["dependencies"]["openclaw"]
    assert platform_compat.DEFAULT_ACPX_VERSION == manifest["dependencies"]["acpx"]


def test_every_lock_entry_has_an_immutable_integrity_checked_source() -> None:
    """Registry entries carry resolved+sha512 integrity; git entries are commit-pinned."""
    git_entries: set[str] = set()
    for key, entry in _lock_packages().items():
        if not key:
            continue
        resolved = entry.get("resolved")
        assert isinstance(resolved, str), key
        if resolved.startswith("git+"):
            git_entries.add(key)
            continue
        assert resolved.startswith("https://registry.npmjs.org/"), key
        integrity = entry.get("integrity")
        assert isinstance(integrity, str) and integrity.startswith("sha512-"), key

    assert git_entries == _REVIEWED_GIT_DEPENDENCIES


def test_quoted_versions_match_the_manifest_everywhere() -> None:
    """Docs, env templates, and workflows must quote the pinned versions."""
    pins = dict(platform_compat.NODE_TOOL_PINS)
    mentions = find_version_mentions(REPO_ROOT)
    stale = [
        f"{mention.path}:{mention.line} {mention.package}@{mention.version}"
        for mention in mentions
        if mention.version != pins[mention.package]
    ]

    assert {mention.package for mention in mentions} == {"openclaw", "acpx"}
    assert stale == [], "run tests/scripts/update_node_tools_lock.py to resync: " + ", ".join(stale)


def test_node_tools_bundle_ships_in_package_data() -> None:
    """The packaged manifest is read at import time, so it must ship in the wheel."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["clawops.assets"]

    assert "platform/node-tools/*" in package_data


@pytest.mark.parametrize(
    ("command", "floating"),
    [
        ("npm install --prefix /tmp/x --no-fund openclaw@2026.3.13", True),
        ("npm --prefix /tmp/x i -g acpx", True),
        ("npm install --prefix <expr> --no-fund --no-audit <expr>", True),
        ("npx -y openclaw@2026.3.13 --version", True),
        ("npm exec --package=acpx@0.3.0 -- acpx --help", True),
        ("echo ready && npm add left-pad", True),
        ("npm ci --omit=dev --no-audit --no-fund", False),
        ("npm --prefix platform/plugins/strongclaw-hypermemory run test:openclaw-host", False),
        ("npm install --package-lock-only --ignore-scripts", False),
        ("npm run install-deps", False),
        ("npx tsc --noEmit", False),
        ("pnpm install openclaw", False),
    ],
)
def test_floating_node_command_detector(command: str, floating: bool) -> None:
    """The detector flags resolving installs and OpenClaw/ACPX npx runs, not locked commands."""
    assert bool(find_floating_node_commands(command)) is floating


def test_ci_never_installs_node_packages_outside_a_lock() -> None:
    """CI installs OpenClaw/ACPX only via `npm ci` of a committed lock, never a floating install."""
    findings = [
        f"{finding.path}: {finding.command}" for finding in find_floating_node_installs(REPO_ROOT)
    ]

    assert findings == [], (
        "install from platform/node-tools with `npm ci` "
        "(tests/utils/helpers/_ci_workflows/memory_plugin.install_locked_node_tools): "
        + ", ".join(findings)
    )
