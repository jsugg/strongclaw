"""Validated, cacheable context envelope artifacts."""

from __future__ import annotations

import dataclasses
import pathlib
from datetime import datetime, timezone

from clawops.app_paths import scoped_state_dir
from clawops.common import canonical_json, load_json, sha256_hex, write_json, write_text
from clawops.context.codebase.service import CodebaseContextService, IndexedFile
from clawops.orchestration import (
    CONTEXT_ENVELOPE_SCHEMA_VERSION,
    ProjectDescriptor,
    WorkspaceDescriptor,
)
from clawops.typed_values import as_mapping, as_mapping_list, as_string_list


class ContextEnvelopeValidationError(ValueError):
    """Raised when a context envelope fails validation."""


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactHash:
    """Stable artifact hash record."""

    path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe record."""
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ContextEnvelopeManifest:
    """Context envelope manifest payload."""

    schema_version: int
    pack_version: int
    project_id: str
    workspace_id: str
    lane: str
    role: str
    context_provider: str
    context_scale: str
    retrieval_modes: tuple[str, ...]
    backend: str
    query: str
    created_at: str
    producer: str
    index_snapshot_id: str
    source_state_hash: str
    body_sha256: str
    artifact_hashes: tuple[ArtifactHash, ...]
    included_paths: tuple[str, ...]
    scm_delta_kind: str
    scm_delta_hash: str
    ttl_seconds: int
    cache_key: str
    prior_pack_ref: str | None
    upstream_artifact_hashes: tuple[ArtifactHash, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe manifest mapping."""
        return {
            "schema_version": self.schema_version,
            "pack_version": self.pack_version,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "lane": self.lane,
            "role": self.role,
            "context_provider": self.context_provider,
            "context_scale": self.context_scale,
            "retrieval_modes": list(self.retrieval_modes),
            "backend": self.backend,
            "query": self.query,
            "created_at": self.created_at,
            "producer": self.producer,
            "index_snapshot_id": self.index_snapshot_id,
            "source_state_hash": self.source_state_hash,
            "body_sha256": self.body_sha256,
            "artifact_hashes": [artifact.to_dict() for artifact in self.artifact_hashes],
            "included_paths": list(self.included_paths),
            "scm_delta_kind": self.scm_delta_kind,
            "scm_delta_hash": self.scm_delta_hash,
            "ttl_seconds": self.ttl_seconds,
            "cache_key": self.cache_key,
            "prior_pack_ref": self.prior_pack_ref,
            "upstream_artifact_hashes": [
                artifact.to_dict() for artifact in self.upstream_artifact_hashes
            ],
        }

    @classmethod
    def from_mapping(cls, payload: object) -> "ContextEnvelopeManifest":
        """Load a manifest from a JSON mapping."""
        payload_mapping = as_mapping(payload, path="context_envelope_manifest")

        def _required_int(name: str) -> int:
            raw_value = payload_mapping.get(name)
            if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                raise TypeError(f"{name} must be an integer")
            return raw_value

        def _required_str(name: str) -> str:
            raw_value = payload_mapping.get(name)
            if not isinstance(raw_value, str):
                raise TypeError(f"{name} must be a string")
            return raw_value

        def _artifact_list(name: str) -> tuple[ArtifactHash, ...]:
            artifact_items = as_mapping_list(payload_mapping.get(name, []), path=name)
            artifacts: list[ArtifactHash] = []
            for item in artifact_items:
                path_value = item.get("path")
                sha_value = item.get("sha256")
                size_bytes_value = item.get("size_bytes")
                if not isinstance(path_value, str):
                    raise TypeError(f"{name}.path must be a string")
                if not isinstance(sha_value, str):
                    raise TypeError(f"{name}.sha256 must be a string")
                if isinstance(size_bytes_value, bool) or not isinstance(size_bytes_value, int):
                    raise TypeError(f"{name}.size_bytes must be an integer")
                artifacts.append(
                    ArtifactHash(
                        path=path_value,
                        sha256=sha_value,
                        size_bytes=size_bytes_value,
                    )
                )
            return tuple(artifacts)

        prior_pack_ref_value = payload_mapping.get("prior_pack_ref")
        if prior_pack_ref_value in {None, ""}:
            prior_pack_ref: str | None = None
        elif isinstance(prior_pack_ref_value, str):
            prior_pack_ref = prior_pack_ref_value
        else:
            raise TypeError("prior_pack_ref must be a string")

        return cls(
            schema_version=_required_int("schema_version"),
            pack_version=_required_int("pack_version"),
            project_id=_required_str("project_id"),
            workspace_id=_required_str("workspace_id"),
            lane=_required_str("lane"),
            role=_required_str("role"),
            context_provider=_required_str("context_provider"),
            context_scale=_required_str("context_scale"),
            retrieval_modes=tuple(
                as_string_list(payload_mapping.get("retrieval_modes", []), path="retrieval_modes")
            ),
            backend=_required_str("backend"),
            query=_required_str("query"),
            created_at=_required_str("created_at"),
            producer=_required_str("producer"),
            index_snapshot_id=_required_str("index_snapshot_id"),
            source_state_hash=_required_str("source_state_hash"),
            body_sha256=_required_str("body_sha256"),
            artifact_hashes=_artifact_list("artifact_hashes"),
            included_paths=as_string_list(
                payload_mapping.get("included_paths", []), path="included_paths"
            ),
            scm_delta_kind=_required_str("scm_delta_kind"),
            scm_delta_hash=_required_str("scm_delta_hash"),
            ttl_seconds=_required_int("ttl_seconds"),
            cache_key=_required_str("cache_key"),
            prior_pack_ref=prior_pack_ref,
            upstream_artifact_hashes=_artifact_list("upstream_artifact_hashes"),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class ContextEnvelope:
    """Materialized context envelope paths and manifest."""

    manifest: ContextEnvelopeManifest
    manifest_path: pathlib.Path
    body_path: pathlib.Path
    diff_path: pathlib.Path | None = None
    reused: bool = False


def _timestamp_text() -> str:
    """Return the current UTC time as ISO-8601 text."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _artifact_hashes_for_paths(
    workspace: WorkspaceDescriptor,
    paths: tuple[pathlib.Path, ...],
) -> tuple[ArtifactHash, ...]:
    """Hash one or more workspace-relative upstream artifacts."""
    records: list[ArtifactHash] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            raise ContextEnvelopeValidationError(f"upstream artifact is missing: {resolved}")
        try:
            relative_path = resolved.relative_to(workspace.working_directory)
            stored_path = relative_path.as_posix()
        except ValueError:
            stored_path = resolved.as_posix()
        if resolved.is_dir():
            size_bytes = 0
            digest_seed = canonical_json(
                sorted(
                    child.relative_to(resolved).as_posix()
                    for child in resolved.rglob("*")
                    if child.is_file()
                )
            )
            digest = sha256_hex(digest_seed)
        else:
            content = resolved.read_text(encoding="utf-8")
            digest = sha256_hex(content)
            size_bytes = resolved.stat().st_size
        records.append(ArtifactHash(path=stored_path, sha256=digest, size_bytes=size_bytes))
    return tuple(records)


def _artifact_hashes_from_records(records: list[IndexedFile]) -> tuple[ArtifactHash, ...]:
    """Convert indexed file records into stable artifact hashes."""
    return tuple(
        ArtifactHash(path=record.path, sha256=record.sha256, size_bytes=record.size_bytes)
        for record in records
    )


def _render_body(
    *,
    manifest: ContextEnvelopeManifest,
    file_records: list[IndexedFile],
    scm_delta_text: str,
    upstream_artifacts: tuple[ArtifactHash, ...],
) -> str:
    """Render the markdown body for a context envelope."""
    lines: list[str] = ["# Context Envelope", ""]
    lines.append(f"- project_id: {manifest.project_id}")
    lines.append(f"- workspace_id: {manifest.workspace_id}")
    lines.append(f"- lane: {manifest.lane}")
    lines.append(f"- role: {manifest.role}")
    lines.append(f"- context_provider: {manifest.context_provider}")
    lines.append(f"- context_scale: {manifest.context_scale}")
    lines.append(f"- retrieval_modes: {', '.join(manifest.retrieval_modes)}")
    lines.append(f"- backend: {manifest.backend}")
    lines.append(f"- context_provider: {manifest.context_provider}")
    lines.append(f"- context_scale: {manifest.context_scale}")
    lines.append(f"- retrieval_modes: {', '.join(manifest.retrieval_modes)}")
    lines.append(f"- query: {manifest.query}")
    lines.append("")
    if scm_delta_text:
        lines.append("## SCM delta")
        lines.append(f"- kind: {manifest.scm_delta_kind}")
        lines.append("```text")
        lines.append(scm_delta_text)
        lines.append("```")
        lines.append("")
    if upstream_artifacts:
        lines.append("## Upstream artifacts")
        for artifact in upstream_artifacts:
            lines.append(f"- {artifact.path} ({artifact.sha256[:12]})")
        lines.append("")
    lines.append("## Retrieved files")
    for record in file_records:
        lines.append(f"### {record.path}")
        if record.symbols:
            lines.append(f"- symbols: {', '.join(record.symbols[:12])}")
        lines.append(f"- sha256: {record.sha256}")
        lines.append("```text")
        lines.append(record.content)
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _snapshot_diff(
    current: tuple[ArtifactHash, ...],
    previous: tuple[ArtifactHash, ...],
) -> dict[str, list[str]]:
    """Compute an artifact diff between two manifests."""
    current_map = {artifact.path: artifact.sha256 for artifact in current}
    previous_map = {artifact.path: artifact.sha256 for artifact in previous}
    added = sorted(path for path in current_map if path not in previous_map)
    removed = sorted(path for path in previous_map if path not in current_map)
    changed = sorted(
        path
        for path in current_map
        if path in previous_map and current_map[path] != previous_map[path]
    )
    return {"added_paths": added, "removed_paths": removed, "changed_paths": changed}


def _load_latest_manifest(
    state_root: pathlib.Path,
) -> tuple[pathlib.Path, ContextEnvelopeManifest] | None:
    """Load the newest manifest across every cache key."""
    candidates = sorted(state_root.glob("*/*/context.manifest.json"))
    if not candidates:
        return None
    latest = candidates[-1]
    payload = load_json(latest)
    return latest, ContextEnvelopeManifest.from_mapping(payload)


class ContextEnvelopeBuilder:
    """Build, reuse, diff, and validate context envelopes."""

    def __init__(
        self,
        service: CodebaseContextService,
        *,
        project: ProjectDescriptor,
        workspace: WorkspaceDescriptor,
        lane: str,
        role: str,
        backend: str,
        provider: str,
        scale: str,
    ) -> None:
        self.service = service
        self.project = project
        self.workspace = workspace
        self.lane = lane
        self.role = role
        self.backend = backend
        self.provider = provider
        self.scale = scale

    def _scm_delta(self, previous_manifest: ContextEnvelopeManifest | None) -> tuple[str, str, str]:
        """Return the SCM delta kind, text, and hash."""
        if self.workspace.kind in {"git_worktree", "git_clone"}:
            diff = self.service.git_diff()
            if diff:
                return "git_diff", diff, sha256_hex(diff)
            return "git_diff", "", sha256_hex("")
        if previous_manifest is None:
            diff_text = canonical_json(
                {"added_paths": [], "removed_paths": [], "changed_paths": []}
            )
            return "snapshot_diff", diff_text, sha256_hex(diff_text)
        current_artifacts = _artifact_hashes_from_records(self.service.snapshot_records())
        diff_payload = _snapshot_diff(current_artifacts, previous_manifest.artifact_hashes)
        diff_text = canonical_json(diff_payload)
        return "snapshot_diff", diff_text, sha256_hex(diff_text)

    def build(
        self,
        *,
        query: str,
        limit: int = 8,
        ttl_seconds: int = 900,
        prior_artifacts: tuple[pathlib.Path, ...] = (),
        output_dir: pathlib.Path | None = None,
    ) -> ContextEnvelope:
        """Build or reuse one context envelope."""
        self.service.index()
        state_root = (
            scoped_state_dir(self.workspace.working_directory, category="context-envelopes")
            / self.role
            if output_dir is None
            else output_dir.expanduser().resolve()
        )
        hits = self.service.query(query, limit=limit)
        file_records = self.service.load_file_records([hit.path for hit in hits])
        artifact_hashes = _artifact_hashes_from_records(file_records)
        index_snapshot_id = self.service.index_snapshot_id()
        source_state_hash = index_snapshot_id
        upstream_artifacts = _artifact_hashes_for_paths(self.workspace, prior_artifacts)
        cache_key = sha256_hex(
            canonical_json(
                {
                    "project_id": self.project.project_id,
                    "workspace_id": self.workspace.workspace_id,
                    "lane": self.lane,
                    "role": self.role,
                    "context_provider": self.provider,
                    "context_scale": self.scale,
                    "retrieval_modes": list(self.service.backend_modes()),
                    "backend": self.backend,
                    "query": query,
                    "index_snapshot_id": index_snapshot_id,
                    "source_state_hash": source_state_hash,
                    "artifact_hashes": [artifact.to_dict() for artifact in artifact_hashes],
                    "upstream_artifact_hashes": [
                        artifact.to_dict() for artifact in upstream_artifacts
                    ],
                }
            )
        )
        previous_latest = _load_latest_manifest(state_root)
        previous_manifest = None if previous_latest is None else previous_latest[1]
        scm_delta_kind, scm_delta_text, scm_delta_hash = self._scm_delta(previous_manifest)
        manifest = ContextEnvelopeManifest(
            schema_version=CONTEXT_ENVELOPE_SCHEMA_VERSION,
            pack_version=1,
            project_id=self.project.project_id,
            workspace_id=self.workspace.workspace_id,
            lane=self.lane,
            role=self.role,
            context_provider=self.provider,
            context_scale=self.scale,
            retrieval_modes=self.service.backend_modes(),
            backend=self.backend,
            query=query,
            created_at=_timestamp_text(),
            producer="clawops.context_envelope",
            index_snapshot_id=index_snapshot_id,
            source_state_hash=source_state_hash,
            body_sha256="",
            artifact_hashes=artifact_hashes,
            included_paths=tuple(artifact.path for artifact in artifact_hashes),
            scm_delta_kind=scm_delta_kind,
            scm_delta_hash=scm_delta_hash,
            ttl_seconds=ttl_seconds,
            cache_key=cache_key,
            prior_pack_ref=None if previous_latest is None else str(previous_latest[0]),
            upstream_artifact_hashes=upstream_artifacts,
        )
        body = _render_body(
            manifest=manifest,
            file_records=file_records,
            scm_delta_text=scm_delta_text,
            upstream_artifacts=upstream_artifacts,
        )
        body_sha256 = sha256_hex(body)
        finalized_manifest = dataclasses.replace(manifest, body_sha256=body_sha256)
        envelope_dir = state_root / cache_key / body_sha256[:12]
        manifest_path = envelope_dir / "context.manifest.json"
        body_path = envelope_dir / "context.body.md"
        if manifest_path.exists() and body_path.exists():
            envelope = ContextEnvelope(
                manifest=finalized_manifest,
                manifest_path=manifest_path,
                body_path=body_path,
                reused=True,
            )
            validate_context_envelope(envelope, service=self.service, workspace=self.workspace)
            return envelope

        write_text(body_path, body)
        write_json(manifest_path, finalized_manifest.to_dict())
        diff_path: pathlib.Path | None = None
        if (
            previous_latest is not None
            and previous_manifest is not None
            and previous_manifest.body_sha256 != body_sha256
        ):
            diff_payload: dict[str, object] = {
                **_snapshot_diff(artifact_hashes, previous_manifest.artifact_hashes)
            }
            diff_payload["previous_body_sha256"] = previous_manifest.body_sha256
            diff_payload["current_body_sha256"] = body_sha256
            diff_payload["previous_scm_delta_hash"] = previous_manifest.scm_delta_hash
            diff_payload["current_scm_delta_hash"] = scm_delta_hash
            diff_path = envelope_dir / "manifest.diff.json"
            write_json(diff_path, diff_payload)
        envelope = ContextEnvelope(
            manifest=finalized_manifest,
            manifest_path=manifest_path,
            body_path=body_path,
            diff_path=diff_path,
        )
        validate_context_envelope(envelope, service=self.service, workspace=self.workspace)
        return envelope


def validate_context_envelope(
    envelope: ContextEnvelope,
    *,
    service: CodebaseContextService | None = None,
    workspace: WorkspaceDescriptor | None = None,
) -> None:
    """Validate one materialized context envelope."""
    if not envelope.manifest_path.exists():
        raise ContextEnvelopeValidationError(
            f"context manifest is missing: {envelope.manifest_path}"
        )
    if not envelope.body_path.exists():
        raise ContextEnvelopeValidationError(f"context body is missing: {envelope.body_path}")
    body_text = envelope.body_path.read_text(encoding="utf-8")
    if sha256_hex(body_text) != envelope.manifest.body_sha256:
        raise ContextEnvelopeValidationError("context body hash mismatch")
    if workspace is not None:
        artifact_map = {artifact.path: artifact for artifact in envelope.manifest.artifact_hashes}
        for relative_path, artifact in artifact_map.items():
            candidate = workspace.working_directory / relative_path
            if not candidate.exists():
                raise ContextEnvelopeValidationError(
                    f"context file is missing: {candidate.resolve()}"
                )
            content = candidate.read_text(encoding="utf-8")
            if sha256_hex(content) != artifact.sha256:
                raise ContextEnvelopeValidationError(
                    f"context file hash mismatch: {candidate.resolve()}"
                )
        for artifact in envelope.manifest.upstream_artifact_hashes:
            candidate = pathlib.Path(artifact.path)
            if not candidate.is_absolute():
                candidate = workspace.working_directory / candidate
            if not candidate.exists():
                raise ContextEnvelopeValidationError(
                    f"upstream artifact is missing: {candidate.resolve()}"
                )
    if service is not None:
        current_snapshot_id = service.index_snapshot_id()
        if current_snapshot_id != envelope.manifest.index_snapshot_id:
            raise ContextEnvelopeValidationError(
                "context index snapshot is stale for the current workspace state"
            )


def load_context_envelope(manifest_path: pathlib.Path) -> ContextEnvelope:
    """Load one context envelope from disk."""
    payload = load_json(manifest_path)
    manifest = ContextEnvelopeManifest.from_mapping(payload)
    body_path = manifest_path.parent / "context.body.md"
    diff_path = manifest_path.parent / "manifest.diff.json"
    return ContextEnvelope(
        manifest=manifest,
        manifest_path=manifest_path,
        body_path=body_path,
        diff_path=diff_path if diff_path.exists() else None,
    )
