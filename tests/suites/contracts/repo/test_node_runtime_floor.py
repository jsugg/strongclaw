"""Contracts keeping the supported Node.js range consistent with the pinned OpenClaw."""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, cast

import pytest
import yaml

from clawops.platform_compat import (
    MINIMUM_NODE_VERSION_TEXT,
    NODE_RELEASE_FLOORS,
    SUPPORTED_NODE_RANGE,
    node_version_supported,
    parse_node_version,
)
from tests.utils.helpers.repo import REPO_ROOT

# A concrete Node 22-26 mention such as `22.19.0`, `22.19.x` or `22.19+`; `.x`/`+` read as `.0`.
_NODE_MENTION_PATTERN = re.compile(r"(?<![\d.])(2[2-6])\.(\d+)(?:\.(\d+)|\.x|\+)")
_WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
_WORKFLOW_PATHS = sorted(_WORKFLOWS_DIR.glob("*.yml"))
_FLOOR_WORKFLOWS = ("compatibility-matrix.yml", "memory-plugin-verification.yml")
_FLOOR_DOCS = (
    "README.md",
    "SETUP_GUIDE.md",
    "platform/docs/HOST_PLATFORMS.md",
    "src/clawops/assets/platform/docs/HOST_PLATFORMS.md",
)


def _lock_entry(name: str) -> dict[str, Any]:
    lock_path = REPO_ROOT / "platform" / "node-tools" / "package-lock.json"
    packages = cast(dict[str, Any], json.loads(lock_path.read_text(encoding="utf-8"))["packages"])
    return cast(dict[str, Any], packages[f"node_modules/{name}"])


def _unsupported_node_mentions(text: str) -> list[str]:
    """Return every concrete Node version mention the pinned OpenClaw would refuse."""
    return [
        match[0]
        for match in _NODE_MENTION_PATTERN.finditer(text)
        if not node_version_supported((int(match[1]), int(match[2]), int(match[3] or 0)))
    ]


def _workflow_node_versions(node: object) -> list[str]:
    """Collect literal `node-version` values (scalars and matrix lists) from a workflow tree."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in cast(dict[object, object], node).items():
            if key == "node-version":
                values = cast(list[object], value) if isinstance(value, list) else [value]
                found.extend(str(item) for item in values if "${{" not in str(item))
            else:
                found.extend(_workflow_node_versions(value))
    elif isinstance(node, list):
        for item in cast(list[object], node):
            found.extend(_workflow_node_versions(item))
    return found


def test_range_matches_the_pinned_openclaw_engine_range() -> None:
    """Bootstrap enforces exactly the Node range the locked OpenClaw declares."""
    assert _lock_entry("openclaw")["engines"]["node"] == SUPPORTED_NODE_RANGE


@pytest.mark.parametrize(
    "workflow_path", _WORKFLOW_PATHS, ids=[path.name for path in _WORKFLOW_PATHS]
)
def test_every_ci_node_version_runs_the_pinned_openclaw(workflow_path: pathlib.Path) -> None:
    """No workflow provisions a Node.js the pinned OpenClaw refuses to start on."""
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    for version_text in _workflow_node_versions(workflow):
        version = parse_node_version(version_text)
        assert version is not None, f"{workflow_path.name}: {version_text}"
        assert node_version_supported(version), f"{workflow_path.name}: {version_text}"


@pytest.mark.parametrize("workflow_name", _FLOOR_WORKFLOWS)
def test_ci_matrices_exercise_the_floor(workflow_name: str) -> None:
    """The oldest leg of each supported line in each compatibility matrix is that line's floor."""
    workflow_path = _WORKFLOWS_DIR / workflow_name
    jobs = cast(dict[str, Any], yaml.safe_load(workflow_path.read_text(encoding="utf-8"))["jobs"])
    legs = [
        version
        for job in jobs.values()
        for leg in cast(
            list[object], job.get("strategy", {}).get("matrix", {}).get("node-version", [])
        )
        if (version := parse_node_version(str(leg))) is not None
    ]

    for floor in NODE_RELEASE_FLOORS:
        line_legs = [leg for leg in legs if leg[0] == floor[0]]
        assert line_legs, f"{workflow_name}: no Node {floor[0]} leg"
        assert min(line_legs) == floor, f"{workflow_name}: Node {floor[0]} floor"


@pytest.mark.parametrize("relative_path", _FLOOR_DOCS)
def test_docs_quote_the_floor_and_no_unsupported_node(relative_path: str) -> None:
    """Operator docs and their packaged mirror state the current floor and no refused release."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert MINIMUM_NODE_VERSION_TEXT in text, relative_path
    assert _unsupported_node_mentions(text) == [], relative_path


def test_bootstrap_error_strings_use_the_range() -> None:
    """No bootstrap message hard-codes a Node version outside the supported range."""
    source = (REPO_ROOT / "src" / "clawops" / "strongclaw_bootstrap.py").read_text(encoding="utf-8")

    assert _unsupported_node_mentions(source) == []
    assert "SUPPORTED_NODE_RANGE" in source
