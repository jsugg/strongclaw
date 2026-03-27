"""Artifact manifest building for devflow stages."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Final

from clawops.common import load_json, sha256_hex, utc_now_ms, write_json
from clawops.devflow_roles import RoleArtifact
from clawops.typed_values import as_mapping, as_mapping_list

ARTIFACT_MANIFEST_SCHEMA_VERSION: Final[int] = 1


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactEntry:
    """Manifest record for one artifact path."""

    name: str
    path: str
    required: bool
    exists: bool
    sha256: str | None
    size_bytes: int | None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe artifact entry."""
        return {
            "name": self.name,
            "path": self.path,
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class StageArtifactManifest:
    """Manifest record for one devflow stage."""

    stage: str
    role: str
    status: str
    artifacts: tuple[ArtifactEntry, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe stage manifest."""
        return {
            "stage": self.stage,
            "role": self.role,
            "status": self.status,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }


def _artifact_status(artifacts: tuple[ArtifactEntry, ...]) -> str:
    """Return the stage artifact status for one stage."""
    if any(artifact.required and not artifact.exists for artifact in artifacts):
        return "missing_artifacts"
    return "validated"


def _build_entry(run_root: pathlib.Path, artifact: RoleArtifact) -> ArtifactEntry:
    """Return one manifest entry relative to the run root."""
    artifact_path = run_root / artifact.path
    if artifact_path.exists() and artifact_path.is_file():
        return ArtifactEntry(
            name=artifact.name,
            path=artifact.path.as_posix(),
            required=artifact.required,
            exists=True,
            sha256=sha256_hex(artifact_path.read_bytes()),
            size_bytes=artifact_path.stat().st_size,
        )
    return ArtifactEntry(
        name=artifact.name,
        path=artifact.path.as_posix(),
        required=artifact.required,
        exists=False,
        sha256=None,
        size_bytes=None,
    )


def build_stage_artifact_manifest(
    *,
    run_id: str,
    run_root: pathlib.Path,
    stage_name: str,
    role: str,
    expected_artifacts: tuple[RoleArtifact, ...],
) -> StageArtifactManifest:
    """Build one stage artifact manifest."""
    del run_id
    artifacts = tuple(_build_entry(run_root, artifact) for artifact in expected_artifacts)
    return StageArtifactManifest(
        stage=stage_name,
        role=role,
        status=_artifact_status(artifacts),
        artifacts=artifacts,
    )


def update_artifact_manifest(
    *,
    manifest_path: pathlib.Path,
    run_id: str,
    stage_manifest: StageArtifactManifest,
) -> dict[str, object]:
    """Create or update the run-level artifact manifest."""
    stages: list[dict[str, object]]
    if manifest_path.exists():
        existing_payload = as_mapping(load_json(manifest_path), path="artifact manifest")
        stages = [
            dict(stage)
            for stage in as_mapping_list(
                existing_payload.get("stages", []), path="artifact manifest.stages"
            )
        ]
    else:
        stages = []
    updated = False
    rendered_stage = stage_manifest.to_dict()
    for index, stage in enumerate(stages):
        if stage.get("stage") == stage_manifest.stage:
            stages[index] = rendered_stage
            updated = True
            break
    if not updated:
        stages.append(rendered_stage)
    manifest_payload: dict[str, object] = {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at_ms": utc_now_ms(),
        "stages": stages,
    }
    write_json(manifest_path, manifest_payload)
    return manifest_payload
