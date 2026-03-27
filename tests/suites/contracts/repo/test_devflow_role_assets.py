"""Contract checks for the devflow role catalog and worker assets."""

from __future__ import annotations

from clawops.devflow_roles import load_role_catalog


def test_devflow_role_catalog_worker_assets_exist_and_stay_in_sync() -> None:
    catalog = load_role_catalog()

    assert {"architect", "developer", "sdet", "qa", "reviewer", "lead"} <= set(catalog.roles)
    for role_name, profile in catalog.roles.items():
        assert profile.worker_prompt.exists(), role_name
        assert profile.expected_artifacts
