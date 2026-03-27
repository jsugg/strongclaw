"""Unit tests for devflow artifact manifests."""

from __future__ import annotations

import pathlib

from clawops.devflow_artifacts import build_stage_artifact_manifest, update_artifact_manifest
from clawops.devflow_roles import RoleArtifact
from clawops.typed_values import as_mapping, as_mapping_list


def test_artifact_manifest_tracks_present_and_missing_files(tmp_path: pathlib.Path) -> None:
    run_root = tmp_path / "run"
    (run_root / "artifacts" / "developer").mkdir(parents=True)
    artifact_path = run_root / "artifacts" / "developer" / "summary.md"
    artifact_path.write_text("done\n", encoding="utf-8")

    stage_manifest = build_stage_artifact_manifest(
        run_id="df_test",
        run_root=run_root,
        stage_name="developer",
        role="developer",
        expected_artifacts=(
            RoleArtifact(
                name="summary", path=pathlib.Path("artifacts/developer/summary.md"), required=True
            ),
            RoleArtifact(
                name="notes", path=pathlib.Path("artifacts/developer/notes.md"), required=True
            ),
        ),
    )

    payload = update_artifact_manifest(
        manifest_path=run_root / "artifacts" / "manifest.json",
        run_id="df_test",
        stage_manifest=stage_manifest,
    )
    stages = as_mapping_list(payload["stages"], path="artifact manifest.stages")
    first_stage = as_mapping(stages[0], path="artifact manifest.stages[0]")
    stage_artifacts = as_mapping_list(
        first_stage["artifacts"], path="artifact manifest.stages[0].artifacts"
    )
    first_artifact = as_mapping(stage_artifacts[0], path="artifact manifest.stages[0].artifacts[0]")
    second_artifact = as_mapping(
        stage_artifacts[1], path="artifact manifest.stages[0].artifacts[1]"
    )

    assert stage_manifest.status == "missing_artifacts"
    assert payload["run_id"] == "df_test"
    assert first_artifact["exists"] is True
    assert second_artifact["exists"] is False
