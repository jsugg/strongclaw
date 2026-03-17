"""Regression tests for dependency submission and release workflows."""

from __future__ import annotations

import pathlib


def test_dependency_submission_workflow_submits_spdx_snapshot() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/dependency-submission.yml").read_text(
        encoding="utf-8"
    )

    assert "contents: write" in workflow
    assert "id-token: write" in workflow
    assert "anchore/sbom-action@57aae528053a48a3f6235f2d9461b05fbcb7366d" in workflow
    assert "dependency-snapshot: true" in workflow
    assert "output-file: sbom.spdx.json" in workflow


def test_release_workflow_builds_assets_and_attests_them() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    workflow = (repo_root / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'tags:\n      - "v*"' in workflow
    assert "python -m build" in workflow
    assert "gh release create" in workflow
    assert "anchore/sbom-action@57aae528053a48a3f6235f2d9461b05fbcb7366d" in workflow
    assert "actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32" in workflow
    assert "actions/attest-sbom@07e74fc4e78d1aad915e867f9a094073a9f71527" in workflow
    assert "sbom-path: sbom.spdx.json" in workflow
