"""Contracts keeping the supported Node.js floor consistent with the pinned OpenClaw."""

from __future__ import annotations

import json
import re
from typing import Any, cast

import pytest
import yaml

from clawops.platform_compat import MINIMUM_NODE_VERSION, MINIMUM_NODE_VERSION_TEXT
from tests.utils.helpers.repo import REPO_ROOT

_ENGINE_FLOOR_PATTERN = re.compile(r"^>=(\d+)\.(\d+)\.\d+$")
_STALE_FLOOR_PATTERN = re.compile(r"\b22\.(?:1[0-8]|[0-9])(?:\.\d+|\.x|\+)")
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


def test_floor_matches_the_pinned_openclaw_engine_range() -> None:
    """Bootstrap enforces exactly the Node floor the locked OpenClaw declares."""
    engine = str(_lock_entry("openclaw")["engines"]["node"])
    match = _ENGINE_FLOOR_PATTERN.fullmatch(engine)

    assert match is not None, engine
    assert (int(match[1]), int(match[2])) == MINIMUM_NODE_VERSION


@pytest.mark.parametrize("workflow_name", _FLOOR_WORKFLOWS)
def test_ci_matrices_exercise_the_floor(workflow_name: str) -> None:
    """The oldest Node 22 leg in each compatibility matrix is the supported floor itself."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / workflow_name
    jobs = cast(dict[str, Any], yaml.safe_load(workflow_path.read_text(encoding="utf-8"))["jobs"])
    legs = [
        str(version)
        for job in jobs.values()
        for version in cast(
            list[object], job.get("strategy", {}).get("matrix", {}).get("node-version", [])
        )
    ]
    node_22_legs = sorted(
        (leg for leg in legs if leg.startswith("22.")),
        key=lambda leg: tuple(int(part) for part in leg.split(".")),
    )

    assert node_22_legs, workflow_name
    assert node_22_legs[0] == f"{MINIMUM_NODE_VERSION_TEXT}.0"


@pytest.mark.parametrize("relative_path", _FLOOR_DOCS)
def test_docs_quote_the_floor_and_no_older_node_22(relative_path: str) -> None:
    """Operator docs and their packaged mirror state the current floor only."""
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert MINIMUM_NODE_VERSION_TEXT in text, relative_path
    assert _STALE_FLOOR_PATTERN.findall(text) == [], relative_path


def test_bootstrap_error_strings_use_the_floor() -> None:
    """No bootstrap message hard-codes an older floor."""
    source = (REPO_ROOT / "src" / "clawops" / "strongclaw_bootstrap.py").read_text(encoding="utf-8")

    assert _STALE_FLOOR_PATTERN.findall(source) == []
    assert "MINIMUM_NODE_VERSION" in source
