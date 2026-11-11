"""Contract checks for Dependabot update coverage."""

from __future__ import annotations

from typing import Any, cast

import yaml

from clawops import platform_compat
from tests.utils.helpers.repo import REPO_ROOT

# Dependabot's parser rejected semver-* cooldown keys for github-actions and docker-compose
# (check run 85120245285); only ecosystems proven to accept them belong here.
_SEMVER_COOLDOWN_ECOSYSTEMS = frozenset({"uv"})


def test_dependabot_tracks_runtime_update_surfaces() -> None:
    """Dependabot should watch Python, Actions, compose images, and the node-tools lock."""
    payload = cast(
        dict[str, Any],
        yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")),
    )
    updates = cast(list[dict[str, Any]], payload["updates"])
    by_ecosystem = {str(entry["package-ecosystem"]): entry for entry in updates}

    assert by_ecosystem["uv"]["directory"] == "/"
    assert by_ecosystem["uv"]["versioning-strategy"] == "increase"
    assert by_ecosystem["github-actions"]["directory"] == "/"
    assert by_ecosystem["docker-compose"]["directories"] == [
        "/platform/compose",
        "/src/clawops/assets/platform/compose",
    ]
    assert by_ecosystem["docker-compose"]["groups"]["runtime-compose-images"]["group-by"] == (
        "dependency-name"
    )
    assert by_ecosystem["npm"]["directories"] == [
        "/platform/node-tools",
        "/src/clawops/assets/platform/node-tools",
    ]
    assert by_ecosystem["npm"]["groups"]["node-tools"]["group-by"] == "dependency-name"


def _npm_update() -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")),
    )
    updates = cast(list[dict[str, Any]], payload["updates"])
    return next(entry for entry in updates if entry["package-ecosystem"] == "npm")


def test_dependabot_holds_openclaw_below_the_node_24_line() -> None:
    """OpenClaw 2026.7+ needs a newer Node than the supported floor, so it is bumped by hand."""
    ignored = {
        str(rule["dependency-name"]): list(rule["versions"])
        for rule in cast(list[dict[str, Any]], _npm_update()["ignore"])
    }
    pinned = tuple(int(part) for part in platform_compat.DEFAULT_OPENCLAW_VERSION.split("."))

    assert ignored == {"openclaw": [">=2026.7.0"], "@openclaw/*": [">=2026.7.0"]}
    assert pinned < (2026, 7, 0)


def test_dependabot_groups_node_tools_security_updates() -> None:
    """Security fixes for the locked tree land as one grouped PR per node-tools directory."""
    groups = cast(dict[str, dict[str, Any]], _npm_update()["groups"])

    assert groups["node-tools-security"] == {
        "applies-to": "security-updates",
        "patterns": ["*"],
    }
    assert "applies-to" not in groups["node-tools"]


def test_dependabot_limits_solo_maintainer_pr_noise() -> None:
    """Version update noise should stay bounded for the sole-maintainer workflow."""
    payload = cast(
        dict[str, Any],
        yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")),
    )
    updates = cast(list[dict[str, Any]], payload["updates"])

    for entry in updates:
        assert entry["open-pull-requests-limit"] == 2
        assert entry["schedule"]["interval"] == "weekly"
        assert entry["schedule"]["timezone"] == "America/Sao_Paulo"


def test_dependabot_cooldown_uses_keys_each_ecosystem_parser_accepts() -> None:
    """An unsupported cooldown key invalidates the whole file and silently stops updates."""
    payload = cast(
        dict[str, Any],
        yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")),
    )
    updates = cast(list[dict[str, Any]], payload["updates"])
    semver_keys = {"semver-major-days", "semver-minor-days", "semver-patch-days"}

    for entry in updates:
        cooldown = cast(dict[str, int], entry["cooldown"])
        if entry["package-ecosystem"] in _SEMVER_COOLDOWN_ECOSYSTEMS:
            assert cooldown["semver-major-days"] >= 14
        else:
            assert semver_keys.isdisjoint(cooldown), entry["package-ecosystem"]
            assert cooldown["default-days"] >= 14
