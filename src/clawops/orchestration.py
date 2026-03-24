"""Workflow orchestration descriptors, identities, and task contracts."""

from __future__ import annotations

import dataclasses
import pathlib
import re
from collections.abc import Mapping, Sequence
from typing import Final, Literal, cast

from clawops.common import canonical_json, sha256_hex
from clawops.process_runner import run_command

ORCHESTRATION_SCHEMA_VERSION: Final[int] = 1
CONTEXT_ENVELOPE_SCHEMA_VERSION: Final[int] = 1

type WorkspaceKind = Literal[
    "git_worktree",
    "git_clone",
    "local_dir",
    "remote_sync_mirror",
    "artifact_bundle",
]
type DeliveryTargetKind = Literal["ssh", "ftp", "rsync", "s3", "manual_bundle"]
type AuthMode = Literal["subscription", "api", "cloud-provider", "local"]

WORKSPACE_KINDS: Final[frozenset[str]] = frozenset(
    {"git_worktree", "git_clone", "local_dir", "remote_sync_mirror", "artifact_bundle"}
)
DELIVERY_TARGET_KINDS: Final[frozenset[str]] = frozenset(
    {"ssh", "ftp", "rsync", "s3", "manual_bundle"}
)
AUTH_MODES: Final[frozenset[str]] = frozenset({"subscription", "api", "cloud-provider", "local"})
ROLE_NAMES: Final[frozenset[str]] = frozenset(
    {"lead", "architect", "developer", "reviewer", "sdet", "qa", "release"}
)


class DescriptorError(ValueError):
    """Raised when a project, workspace, or delivery target is invalid."""


def _slugify(value: str) -> str:
    """Return a filesystem-safe slug."""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return normalized or "scope"


def _validate_token(name: str, value: str, *, allow_colon: bool = False) -> str:
    """Validate a user-facing identity token."""
    if not value.strip():
        raise DescriptorError(f"{name} must not be empty")
    pattern = r"^[A-Za-z0-9._:-]+$" if allow_colon else r"^[A-Za-z0-9._-]+$"
    if re.fullmatch(pattern, value) is None:
        raise DescriptorError(f"{name} contains unsupported characters: {value!r}")
    return value


def _resolve_path(base_dir: pathlib.Path, value: object, *, field_name: str) -> pathlib.Path:
    """Resolve a path-like mapping field against *base_dir*."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    raw_path = pathlib.Path(value).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (base_dir / raw_path).resolve()


def _dedupe_paths(paths: Sequence[pathlib.Path]) -> tuple[pathlib.Path, ...]:
    """Return a stable tuple of unique resolved paths."""
    deduped: list[pathlib.Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        key = resolved.as_posix()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return tuple(deduped)


def _git_stdout(path: pathlib.Path, *arguments: str) -> str | None:
    """Run a git query against *path* and return stripped stdout."""
    result = run_command(["git", "-C", str(path), *arguments], timeout_seconds=10)
    if not result.ok:
        return None
    return result.stdout.strip()


def _stable_descriptor_id(prefix: str, seed: str) -> str:
    """Return a deterministic descriptor id for a path or locator."""
    return f"{_slugify(prefix)}-{sha256_hex(seed)[:12]}"


def _within_any_root(path: pathlib.Path, roots: Sequence[pathlib.Path]) -> pathlib.Path:
    """Return the trusted root containing *path* or raise."""
    resolved = path.expanduser().resolve()
    for root in roots:
        trusted_root = root.expanduser().resolve()
        try:
            resolved.relative_to(trusted_root)
        except ValueError:
            continue
        return trusted_root
    roots_text = ", ".join(root.expanduser().resolve().as_posix() for root in roots)
    raise DescriptorError(f"path {resolved} must stay under one of: {roots_text}")


@dataclasses.dataclass(frozen=True, slots=True)
class ProjectDescriptor:
    """Canonical orchestration project identity."""

    project_id: str
    root: pathlib.Path
    trusted_roots: tuple[pathlib.Path, ...]
    metadata: Mapping[str, object] = dataclasses.field(default_factory=dict)

    @classmethod
    def resolve(
        cls,
        root: pathlib.Path,
        *,
        project_id: str | None = None,
        trusted_roots: Sequence[pathlib.Path] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> "ProjectDescriptor":
        """Resolve and validate a project descriptor."""
        resolved_root = root.expanduser().resolve()
        if not resolved_root.exists():
            raise DescriptorError(f"project root does not exist: {resolved_root}")
        if not resolved_root.is_dir():
            raise DescriptorError(f"project root is not a directory: {resolved_root}")
        descriptor_id = (
            _validate_token("project_id", project_id)
            if project_id
            else _stable_descriptor_id(resolved_root.name, resolved_root.as_posix())
        )
        resolved_trusted_roots = _dedupe_paths((resolved_root, *trusted_roots))
        return cls(
            project_id=descriptor_id,
            root=resolved_root,
            trusted_roots=resolved_trusted_roots,
            metadata=dict(metadata or {}),
        )

    def contains(self, path: pathlib.Path) -> bool:
        """Return True when *path* stays under a trusted root."""
        try:
            _within_any_root(path, self.trusted_roots)
        except DescriptorError:
            return False
        return True


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceDescriptor:
    """Canonical workspace identity."""

    project_id: str
    workspace_id: str
    kind: WorkspaceKind
    root: pathlib.Path
    working_directory: pathlib.Path
    trusted_root: pathlib.Path
    branch: str | None = None
    metadata: Mapping[str, object] = dataclasses.field(default_factory=dict)

    @classmethod
    def resolve(
        cls,
        project: ProjectDescriptor,
        *,
        kind: str,
        path: pathlib.Path,
        workspace_id: str | None = None,
        branch: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> "WorkspaceDescriptor":
        """Resolve and validate a workspace descriptor."""
        if kind not in WORKSPACE_KINDS:
            raise DescriptorError(
                f"workspace kind must be one of: {', '.join(sorted(WORKSPACE_KINDS))}"
            )
        resolved_path = path.expanduser().resolve()
        if not resolved_path.exists():
            raise DescriptorError(f"workspace path does not exist: {resolved_path}")
        if kind == "artifact_bundle":
            if not (resolved_path.is_file() or resolved_path.is_dir()):
                raise DescriptorError(
                    f"artifact bundle must be a file or directory: {resolved_path}"
                )
            working_directory = resolved_path if resolved_path.is_dir() else resolved_path.parent
        else:
            if not resolved_path.is_dir():
                raise DescriptorError(f"workspace path must be a directory: {resolved_path}")
            working_directory = resolved_path
        trusted_root = _within_any_root(resolved_path, project.trusted_roots)

        resolved_branch = branch
        workspace_metadata: dict[str, object] = dict(metadata or {})
        if kind in {"git_worktree", "git_clone"}:
            actual_root = _git_stdout(working_directory, "rev-parse", "--show-toplevel")
            if actual_root is None:
                raise DescriptorError(f"workspace is not a git checkout: {working_directory}")
            workspace_metadata.setdefault("git_root", actual_root)
            actual_branch = _git_stdout(working_directory, "symbolic-ref", "--short", "HEAD")
            if resolved_branch is None:
                resolved_branch = actual_branch
            elif actual_branch is not None and actual_branch != resolved_branch:
                raise DescriptorError(
                    f"workspace branch mismatch: expected {resolved_branch}, found {actual_branch}"
                )
        descriptor_id = (
            _validate_token("workspace_id", workspace_id)
            if workspace_id
            else _stable_descriptor_id(
                cast(str, kind),
                f"{resolved_path.as_posix()}:{resolved_branch or 'no-branch'}",
            )
        )
        return cls(
            project_id=project.project_id,
            workspace_id=descriptor_id,
            kind=cast(WorkspaceKind, kind),
            root=resolved_path,
            working_directory=working_directory,
            trusted_root=trusted_root,
            branch=resolved_branch,
            metadata=workspace_metadata,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class DeliveryTargetDescriptor:
    """Canonical delivery target identity."""

    project_id: str
    target_id: str
    kind: DeliveryTargetKind
    locator: str
    metadata: Mapping[str, object] = dataclasses.field(default_factory=dict)

    @classmethod
    def resolve(
        cls,
        project: ProjectDescriptor,
        *,
        kind: str,
        locator: str,
        target_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> "DeliveryTargetDescriptor":
        """Resolve and validate a delivery target."""
        if kind not in DELIVERY_TARGET_KINDS:
            raise DescriptorError(
                f"delivery target kind must be one of: {', '.join(sorted(DELIVERY_TARGET_KINDS))}"
            )
        if not locator.strip():
            raise DescriptorError("delivery target locator must not be empty")
        if kind == "manual_bundle":
            bundle_path = pathlib.Path(locator).expanduser().resolve()
            if not bundle_path.exists():
                raise DescriptorError(f"manual bundle does not exist: {bundle_path}")
            _within_any_root(bundle_path, project.trusted_roots)
        descriptor_id = (
            _validate_token("target_id", target_id)
            if target_id
            else _stable_descriptor_id(kind, locator)
        )
        return cls(
            project_id=project.project_id,
            target_id=descriptor_id,
            kind=cast(DeliveryTargetKind, kind),
            locator=locator,
            metadata=dict(metadata or {}),
        )


def build_session_identity(
    *,
    backend: str,
    project_id: str,
    workspace_id: str,
    lane: str,
    role: str,
) -> str:
    """Return the canonical session identity."""
    return ":".join(
        (
            _validate_token("backend", backend, allow_colon=False),
            _validate_token("project_id", project_id),
            _validate_token("workspace_id", workspace_id),
            _validate_token("lane", lane),
            _validate_token("role", role),
        )
    )


def build_lock_identity(
    *,
    project_id: str,
    workspace_id: str,
    lane: str,
    role: str,
    operation_kind: str,
) -> str:
    """Return the canonical lock identity."""
    return ":".join(
        (
            _validate_token("project_id", project_id),
            _validate_token("workspace_id", workspace_id),
            _validate_token("lane", lane),
            _validate_token("role", role),
            _validate_token("operation_kind", operation_kind),
        )
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ContextRequest:
    """Context envelope assembly inputs for a workflow task."""

    config_path: pathlib.Path
    query: str
    limit: int = 8
    ttl_seconds: int = 900
    prior_artifacts: tuple[pathlib.Path, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class ExpectedArtifact:
    """Role-scoped artifact expectation."""

    name: str
    role: str
    path: pathlib.Path
    required: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class OrchestrationTask:
    """Fully resolved orchestration task contract."""

    project: ProjectDescriptor
    workspace: WorkspaceDescriptor
    lane: str
    role: str
    backend: str
    backend_profile: str | None
    prompt: str
    operation_kind: str
    allowed_capabilities: tuple[str, ...]
    required_auth_mode: AuthMode
    approval_required: bool
    timeout_seconds: int
    retry_budget: int
    context_request: ContextRequest | None = None
    expected_artifacts: tuple[ExpectedArtifact, ...] = ()
    delivery_target: DeliveryTargetDescriptor | None = None

    @property
    def session_identity(self) -> str:
        """Return the canonical task session identity."""
        return build_session_identity(
            backend=self.backend,
            project_id=self.project.project_id,
            workspace_id=self.workspace.workspace_id,
            lane=self.lane,
            role=self.role,
        )

    @property
    def lock_identity(self) -> str:
        """Return the canonical task lock identity."""
        return build_lock_identity(
            project_id=self.project.project_id,
            workspace_id=self.workspace.workspace_id,
            lane=self.lane,
            role=self.role,
            operation_kind=self.operation_kind,
        )

    def to_contract(self) -> dict[str, object]:
        """Return a JSON-safe task contract."""
        payload: dict[str, object] = {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "project_id": self.project.project_id,
            "workspace_id": self.workspace.workspace_id,
            "workspace_kind": self.workspace.kind,
            "lane": self.lane,
            "role": self.role,
            "backend": self.backend,
            "backend_profile": self.backend_profile,
            "prompt_sha256": sha256_hex(self.prompt),
            "operation_kind": self.operation_kind,
            "allowed_capabilities": list(self.allowed_capabilities),
            "required_auth_mode": self.required_auth_mode,
            "approval_required": self.approval_required,
            "timeout_seconds": self.timeout_seconds,
            "retry_budget": self.retry_budget,
            "expected_artifacts": [
                {
                    "name": artifact.name,
                    "role": artifact.role,
                    "path": str(artifact.path),
                    "required": artifact.required,
                }
                for artifact in self.expected_artifacts
            ],
        }
        if self.context_request is not None:
            payload["context"] = {
                "config_path": str(self.context_request.config_path),
                "query": self.context_request.query,
                "limit": self.context_request.limit,
                "ttl_seconds": self.context_request.ttl_seconds,
                "prior_artifacts": [str(path) for path in self.context_request.prior_artifacts],
            }
        if self.delivery_target is not None:
            payload["delivery_target"] = {
                "target_id": self.delivery_target.target_id,
                "kind": self.delivery_target.kind,
                "locator": self.delivery_target.locator,
            }
        return payload


def _parse_expected_artifacts(
    value: object,
    *,
    base_dir: pathlib.Path,
) -> tuple[ExpectedArtifact, ...]:
    """Parse a task's expected artifact list."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError("task.expected_artifacts must be a list")
    artifacts: list[ExpectedArtifact] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise TypeError(f"task.expected_artifacts[{index}] must be a mapping")
        name = item.get("name")
        role = item.get("role")
        if not isinstance(name, str) or not name:
            raise TypeError(f"task.expected_artifacts[{index}].name must be a non-empty string")
        if not isinstance(role, str) or role not in ROLE_NAMES:
            allowed_roles = ", ".join(sorted(ROLE_NAMES))
            raise ValueError(
                f"task.expected_artifacts[{index}].role must be one of: {allowed_roles}"
            )
        artifact_path = _resolve_path(
            base_dir, item.get("path"), field_name=f"task.expected_artifacts[{index}].path"
        )
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise TypeError(f"task.expected_artifacts[{index}].required must be a boolean")
        artifacts.append(
            ExpectedArtifact(name=name, role=role, path=artifact_path, required=required)
        )
    return tuple(artifacts)


def _parse_context_request(
    value: object,
    *,
    base_dir: pathlib.Path,
) -> ContextRequest | None:
    """Parse an optional task context request."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("task.context must be a mapping")
    config_path = _resolve_path(base_dir, value.get("config"), field_name="task.context.config")
    query = value.get("query")
    if not isinstance(query, str) or not query.strip():
        raise TypeError("task.context.query must be a non-empty string")
    limit = value.get("limit", 8)
    ttl_seconds = value.get("ttl_seconds", 900)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("task.context.limit must be a positive integer")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise ValueError("task.context.ttl_seconds must be a positive integer")
    prior_artifacts_raw = value.get("prior_artifacts", [])
    if not isinstance(prior_artifacts_raw, list) or not all(
        isinstance(item, str) for item in prior_artifacts_raw
    ):
        raise TypeError("task.context.prior_artifacts must be a list of strings")
    prior_artifacts = tuple(
        _resolve_path(base_dir, item, field_name="task.context.prior_artifacts")
        for item in prior_artifacts_raw
    )
    return ContextRequest(
        config_path=config_path,
        query=query,
        limit=limit,
        ttl_seconds=ttl_seconds,
        prior_artifacts=prior_artifacts,
    )


def resolve_orchestration_task(
    task: object,
    *,
    base_dir: pathlib.Path,
) -> OrchestrationTask:
    """Resolve a workflow task mapping into a typed orchestration contract."""
    if not isinstance(task, dict):
        raise TypeError("task must be a mapping")
    project_payload = task.get("project")
    if not isinstance(project_payload, dict):
        raise TypeError("task.project must be a mapping")
    project_root = _resolve_path(
        base_dir, project_payload.get("root"), field_name="task.project.root"
    )
    trusted_roots_raw = project_payload.get("trusted_roots", [])
    if not isinstance(trusted_roots_raw, list) or not all(
        isinstance(item, str) for item in trusted_roots_raw
    ):
        raise TypeError("task.project.trusted_roots must be a list of strings")
    trusted_roots = tuple(
        _resolve_path(base_dir, item, field_name="task.project.trusted_roots")
        for item in trusted_roots_raw
    )
    project = ProjectDescriptor.resolve(
        project_root,
        project_id=project_payload.get("id"),
        trusted_roots=trusted_roots,
        metadata=(
            project_payload.get("metadata")
            if isinstance(project_payload.get("metadata"), dict)
            else None
        ),
    )

    workspace_payload = task.get("workspace")
    if not isinstance(workspace_payload, dict):
        raise TypeError("task.workspace must be a mapping")
    workspace_path = _resolve_path(
        base_dir, workspace_payload.get("path"), field_name="task.workspace.path"
    )
    workspace = WorkspaceDescriptor.resolve(
        project,
        kind=str(workspace_payload.get("kind")),
        path=workspace_path,
        workspace_id=workspace_payload.get("id"),
        branch=(
            workspace_payload.get("branch")
            if isinstance(workspace_payload.get("branch"), str)
            else None
        ),
        metadata=(
            workspace_payload.get("metadata")
            if isinstance(workspace_payload.get("metadata"), dict)
            else None
        ),
    )

    lane = task.get("lane", "default")
    role = task.get("role")
    backend = task.get("backend")
    prompt = task.get("prompt")
    operation_kind = task.get("operation_kind", "worker_dispatch")
    if not isinstance(lane, str):
        raise TypeError("task.lane must be a string")
    if not isinstance(role, str) or role not in ROLE_NAMES:
        allowed_roles = ", ".join(sorted(ROLE_NAMES))
        raise ValueError(f"task.role must be one of: {allowed_roles}")
    if not isinstance(backend, str) or not backend:
        raise TypeError("task.backend must be a non-empty string")
    if not isinstance(prompt, str) or not prompt.strip():
        raise TypeError("task.prompt must be a non-empty string")
    if not isinstance(operation_kind, str) or not operation_kind:
        raise TypeError("task.operation_kind must be a non-empty string")

    required_auth_mode = task.get("required_auth_mode", "subscription")
    if not isinstance(required_auth_mode, str) or required_auth_mode not in AUTH_MODES:
        allowed_modes = ", ".join(sorted(AUTH_MODES))
        raise ValueError(f"task.required_auth_mode must be one of: {allowed_modes}")
    timeout_seconds = task.get("timeout_seconds", 1800)
    retry_budget = task.get("retry_budget", 0)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise ValueError("task.timeout_seconds must be a positive integer")
    if isinstance(retry_budget, bool) or not isinstance(retry_budget, int) or retry_budget < 0:
        raise ValueError("task.retry_budget must be a non-negative integer")

    allowed_capabilities_raw = task.get("allowed_capabilities", [])
    if not isinstance(allowed_capabilities_raw, list) or not all(
        isinstance(item, str) for item in allowed_capabilities_raw
    ):
        raise TypeError("task.allowed_capabilities must be a list of strings")

    delivery_target_payload = task.get("delivery_target")
    delivery_target: DeliveryTargetDescriptor | None = None
    if delivery_target_payload is not None:
        if not isinstance(delivery_target_payload, dict):
            raise TypeError("task.delivery_target must be a mapping")
        locator = delivery_target_payload.get("locator", delivery_target_payload.get("path"))
        if not isinstance(locator, str):
            raise TypeError("task.delivery_target.locator must be a string")
        delivery_target = DeliveryTargetDescriptor.resolve(
            project,
            kind=str(delivery_target_payload.get("kind")),
            locator=locator,
            target_id=delivery_target_payload.get("id"),
            metadata=(
                delivery_target_payload.get("metadata")
                if isinstance(delivery_target_payload.get("metadata"), dict)
                else None
            ),
        )

    return OrchestrationTask(
        project=project,
        workspace=workspace,
        lane=_validate_token("task.lane", lane),
        role=role,
        backend=backend,
        backend_profile=(
            task.get("backend_profile") if isinstance(task.get("backend_profile"), str) else None
        ),
        prompt=prompt,
        operation_kind=_validate_token("task.operation_kind", operation_kind),
        allowed_capabilities=tuple(allowed_capabilities_raw),
        required_auth_mode=cast(AuthMode, required_auth_mode),
        approval_required=bool(task.get("approval_required", False)),
        timeout_seconds=timeout_seconds,
        retry_budget=retry_budget,
        context_request=_parse_context_request(task.get("context"), base_dir=base_dir),
        expected_artifacts=_parse_expected_artifacts(
            task.get("expected_artifacts"), base_dir=base_dir
        ),
        delivery_target=delivery_target,
    )


def task_contract_hash(task: OrchestrationTask) -> str:
    """Return a stable hash for the orchestration contract."""
    return sha256_hex(canonical_json(task.to_contract()))
