"""Unit tests for the devflow role catalog."""

from __future__ import annotations

import pathlib

import pytest

from clawops.common import write_yaml
from clawops.devflow_roles import load_role_catalog


def test_load_role_catalog_exposes_expected_roles() -> None:
    catalog = load_role_catalog()

    assert {"architect", "developer", "sdet", "qa", "reviewer", "lead"} <= set(catalog.roles)
    assert catalog.role("lead").approval_required is True
    assert catalog.role("architect").workspace_mode == "verify_only"


def test_load_role_catalog_rejects_unknown_workspace_mode(tmp_path: pathlib.Path) -> None:
    catalog_path = tmp_path / "roles.yaml"
    write_yaml(
        catalog_path,
        {
            "schema_version": 1,
            "default_run_profile": "production",
            "roles": {
                "architect": {
                    "worker_prompt": "platform/workers/acpx/architect-system.md",
                    "default_backend": "claude",
                    "permissions_mode": "approve-reads",
                    "required_auth_mode": "subscription",
                    "workspace_mode": "mutates-everything",
                    "mutable_tracked_files": False,
                    "expected_artifacts": [],
                }
            },
        },
    )

    with pytest.raises(ValueError, match="workspace_mode"):
        load_role_catalog(path=catalog_path)
