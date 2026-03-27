"""Config-driven devflow role catalog loading."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Final, Literal, cast

from clawops.acpx_adapter import AcpxPermissionMode
from clawops.backend_registry import resolve_backend
from clawops.common import load_yaml
from clawops.orchestration import AUTH_MODES, ROLE_NAMES, AuthMode
from clawops.typed_values import as_bool, as_mapping, as_mapping_list, as_string

type WorkspaceMode = Literal["mutable_primary", "mutable_test", "verify_only", "read_only"]

WORKSPACE_MODES: Final[frozenset[str]] = frozenset(
    {"mutable_primary", "mutable_test", "verify_only", "read_only"}
)
PERMISSION_MODES: Final[frozenset[str]] = frozenset({"approve-all", "approve-reads", "deny-all"})
DEFAULT_ROLE_CATALOG_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[2] / "platform/configs/devflow/roles.yaml"
)


@dataclasses.dataclass(frozen=True, slots=True)
class RoleArtifact:
    """One artifact requirement for a role."""

    name: str
    path: pathlib.Path
    required: bool

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe artifact payload."""
        return {
            "name": self.name,
            "path": self.path.as_posix(),
            "required": self.required,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class RoleProfile:
    """Validated devflow role profile."""

    name: str
    worker_prompt: pathlib.Path
    default_backend: str
    permissions_mode: AcpxPermissionMode
    required_auth_mode: AuthMode
    workspace_mode: WorkspaceMode
    mutable_tracked_files: bool
    approval_required: bool
    expected_artifacts: tuple[RoleArtifact, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe role profile."""
        return {
            "name": self.name,
            "worker_prompt": self.worker_prompt.as_posix(),
            "default_backend": self.default_backend,
            "permissions_mode": self.permissions_mode,
            "required_auth_mode": self.required_auth_mode,
            "workspace_mode": self.workspace_mode,
            "mutable_tracked_files": self.mutable_tracked_files,
            "approval_required": self.approval_required,
            "expected_artifacts": [artifact.to_dict() for artifact in self.expected_artifacts],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class RoleCatalog:
    """Resolved devflow role catalog."""

    schema_version: int
    default_run_profile: str
    roles: dict[str, RoleProfile]
    source_path: pathlib.Path

    def role(self, name: str) -> RoleProfile:
        """Return one role profile or fail closed."""
        try:
            return self.roles[name]
        except KeyError as exc:
            supported = ", ".join(sorted(self.roles))
            raise KeyError(f"unknown devflow role {name!r}; supported roles: {supported}") from exc

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe role catalog."""
        return {
            "schema_version": self.schema_version,
            "default_run_profile": self.default_run_profile,
            "roles": {name: profile.to_dict() for name, profile in sorted(self.roles.items())},
            "source_path": self.source_path.as_posix(),
        }


def _repo_root() -> pathlib.Path:
    """Return the repository root from the installed source tree."""
    return pathlib.Path(__file__).resolve().parents[2]


def _validate_artifacts(
    payload: object,
    *,
    role_name: str,
    repo_root: pathlib.Path,
) -> tuple[RoleArtifact, ...]:
    """Return validated artifact requirements for one role."""
    artifacts: list[RoleArtifact] = []
    for index, artifact in enumerate(
        as_mapping_list(payload, path=f"roles.{role_name}.expected_artifacts")
    ):
        name = as_string(
            artifact.get("name"), path=f"roles.{role_name}.expected_artifacts[{index}].name"
        )
        raw_path = as_string(
            artifact.get("path"), path=f"roles.{role_name}.expected_artifacts[{index}].path"
        )
        required = as_bool(
            artifact.get("required", True),
            path=f"roles.{role_name}.expected_artifacts[{index}].required",
        )
        artifact_path = pathlib.Path(raw_path)
        if artifact_path.is_absolute():
            raise ValueError(
                f"roles.{role_name}.expected_artifacts[{index}].path must be relative to run root"
            )
        normalized_path = artifact_path.as_posix()
        if not normalized_path.startswith("artifacts/"):
            raise ValueError(
                f"roles.{role_name}.expected_artifacts[{index}].path must stay under artifacts/: {raw_path}"
            )
        artifacts.append(RoleArtifact(name=name, path=artifact_path, required=required))
    return tuple(artifacts)


def load_role_catalog(path: pathlib.Path | None = None) -> RoleCatalog:
    """Load and validate the devflow role catalog."""
    catalog_path = (DEFAULT_ROLE_CATALOG_PATH if path is None else path).expanduser().resolve()
    payload = as_mapping(load_yaml(catalog_path), path="role catalog")
    repo_root = _repo_root()
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise ValueError(f"unsupported role catalog schema version: {schema_version!r}")
    default_run_profile = as_string(
        payload.get("default_run_profile"),
        path="role catalog.default_run_profile",
    )
    roles_payload = as_mapping(payload.get("roles"), path="role catalog.roles")
    roles: dict[str, RoleProfile] = {}
    for role_name, raw_profile in sorted(roles_payload.items()):
        if role_name not in ROLE_NAMES:
            allowed = ", ".join(sorted(ROLE_NAMES))
            raise ValueError(f"unknown devflow role {role_name!r}; supported roles: {allowed}")
        profile = as_mapping(raw_profile, path=f"role catalog.roles.{role_name}")
        worker_prompt_raw = as_string(
            profile.get("worker_prompt"),
            path=f"role catalog.roles.{role_name}.worker_prompt",
        )
        worker_prompt = (repo_root / worker_prompt_raw).expanduser().resolve()
        if not worker_prompt.exists():
            raise FileNotFoundError(
                f"devflow worker prompt for role {role_name!r} does not exist: {worker_prompt}"
            )
        default_backend = as_string(
            profile.get("default_backend"),
            path=f"role catalog.roles.{role_name}.default_backend",
        )
        resolve_backend(default_backend)
        permissions_mode = as_string(
            profile.get("permissions_mode"),
            path=f"role catalog.roles.{role_name}.permissions_mode",
        )
        if permissions_mode not in PERMISSION_MODES:
            allowed_permissions = ", ".join(sorted(PERMISSION_MODES))
            raise ValueError(
                f"role catalog.roles.{role_name}.permissions_mode must be one of: "
                f"{allowed_permissions}"
            )
        required_auth_mode = as_string(
            profile.get("required_auth_mode"),
            path=f"role catalog.roles.{role_name}.required_auth_mode",
        )
        if required_auth_mode not in AUTH_MODES:
            allowed_auth = ", ".join(sorted(AUTH_MODES))
            raise ValueError(
                f"role catalog.roles.{role_name}.required_auth_mode must be one of: {allowed_auth}"
            )
        workspace_mode = as_string(
            profile.get("workspace_mode"),
            path=f"role catalog.roles.{role_name}.workspace_mode",
        )
        if workspace_mode not in WORKSPACE_MODES:
            allowed_workspace_modes = ", ".join(sorted(WORKSPACE_MODES))
            raise ValueError(
                f"role catalog.roles.{role_name}.workspace_mode must be one of: "
                f"{allowed_workspace_modes}"
            )
        roles[role_name] = RoleProfile(
            name=role_name,
            worker_prompt=worker_prompt,
            default_backend=default_backend,
            permissions_mode=cast(AcpxPermissionMode, permissions_mode),
            required_auth_mode=cast(AuthMode, required_auth_mode),
            workspace_mode=cast(WorkspaceMode, workspace_mode),
            mutable_tracked_files=as_bool(
                profile.get("mutable_tracked_files"),
                path=f"role catalog.roles.{role_name}.mutable_tracked_files",
            ),
            approval_required=as_bool(
                profile.get("approval_required", False),
                path=f"role catalog.roles.{role_name}.approval_required",
            ),
            expected_artifacts=_validate_artifacts(
                profile.get("expected_artifacts", []),
                role_name=role_name,
                repo_root=repo_root,
            ),
        )
    return RoleCatalog(
        schema_version=1,
        default_run_profile=default_run_profile,
        roles=roles,
        source_path=catalog_path,
    )


def resolve_role_profile(name: str, *, path: pathlib.Path | None = None) -> RoleProfile:
    """Resolve one devflow role profile from the catalog."""
    return load_role_catalog(path=path).role(name)
