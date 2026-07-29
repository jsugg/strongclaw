"""Contract checks for Dependabot update coverage."""

from __future__ import annotations

from typing import Any, cast

import yaml

from tests.utils.helpers.repo import REPO_ROOT


def test_dependabot_tracks_runtime_update_surfaces() -> None:
    """Dependabot should watch Python, Actions, and shipped compose image surfaces."""
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
        assert entry["cooldown"]["semver-major-days"] >= 14
