"""Typed devflow plan and CLI contract helpers."""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Sequence
from typing import Final, cast

from clawops.acpx_adapter import AcpxPermissionMode
from clawops.common import canonical_json, load_json, load_yaml, sha256_hex
from clawops.devflow_roles import (
    RoleArtifact,
    RoleCatalog,
    RoleProfile,
    WorkspaceMode,
    load_role_catalog,
)
from clawops.orchestration import AuthMode
from clawops.typed_values import (
    as_bool,
    as_int,
    as_mapping,
    as_mapping_list,
    as_string,
    as_string_list,
)
from clawops.workspace_bootstrap import BootstrapProfile, resolve_bootstrap_profile

DEVFLOW_PLAN_SCHEMA_VERSION: Final[int] = 1
DEFAULT_WORKFLOW_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[2]
    / "platform/configs/devflow/workflows/production.yaml"
)


@dataclasses.dataclass(frozen=True, slots=True)
class DevflowStagePlan:
    """Planned execution contract for one stage."""

    name: str
    role: str
    backend: str
    permissions_mode: AcpxPermissionMode
    workspace_mode: WorkspaceMode
    retry_budget: int
    expected_artifacts: tuple[RoleArtifact, ...]
    approval_required: bool
    required_auth_mode: AuthMode
    worker_prompt: pathlib.Path

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe stage plan."""
        return {
            "name": self.name,
            "role": self.role,
            "backend": self.backend,
            "permissions_mode": self.permissions_mode,
            "workspace_mode": self.workspace_mode,
            "retry_budget": self.retry_budget,
            "approval_required": self.approval_required,
            "required_auth_mode": self.required_auth_mode,
            "worker_prompt": self.worker_prompt.as_posix(),
            "expected_artifacts": [artifact.to_dict() for artifact in self.expected_artifacts],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DevflowPlan:
    """Devflow plan emitted by ``clawops devflow plan``."""

    schema_version: int
    run_id: str
    goal: str
    repo_root: pathlib.Path
    project_id: str
    lane: str
    run_profile: str
    bootstrap_profile: str
    workflow_path: pathlib.Path
    bootstrap_commands: dict[str, tuple[tuple[str, ...], ...]]
    stages: tuple[DevflowStagePlan, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe plan payload."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "goal": self.goal,
            "repo_root": self.repo_root.as_posix(),
            "project_id": self.project_id,
            "lane": self.lane,
            "run_profile": self.run_profile,
            "bootstrap_profile": self.bootstrap_profile,
            "workflow_path": self.workflow_path.as_posix(),
            "bootstrap_commands": {
                name: [list(command) for command in commands]
                for name, commands in self.bootstrap_commands.items()
            },
            "stages": [stage.to_dict() for stage in self.stages],
        }


def deterministic_run_id(*, repo_root: pathlib.Path, goal: str, lane: str) -> str:
    """Return a deterministic run identifier for one plan request."""
    digest = sha256_hex(
        canonical_json(
            {
                "repo_root": repo_root.expanduser().resolve().as_posix(),
                "goal": goal.strip(),
                "lane": lane.strip(),
            }
        )
    )[:12]
    return f"df_{digest}"


def devflow_run_root(repo_root: pathlib.Path, run_id: str) -> pathlib.Path:
    """Return the canonical on-disk run root."""
    return repo_root.expanduser().resolve() / ".clawops" / "devflow" / run_id


def _load_workflow_profile(path: pathlib.Path) -> dict[str, object]:
    """Load the devflow workflow profile."""
    return dict(as_mapping(load_yaml(path), path="devflow workflow profile"))


def _stage_plan(profile: RoleProfile) -> DevflowStagePlan:
    """Convert one role profile into a stage plan."""
    return DevflowStagePlan(
        name=profile.name,
        role=profile.name,
        backend=profile.default_backend,
        permissions_mode=profile.permissions_mode,
        workspace_mode=profile.workspace_mode,
        retry_budget=0,
        expected_artifacts=profile.expected_artifacts,
        approval_required=profile.approval_required,
        required_auth_mode=profile.required_auth_mode,
        worker_prompt=profile.worker_prompt,
    )


def build_devflow_plan(
    *,
    repo_root: pathlib.Path,
    goal: str,
    lane: str,
    run_id: str | None = None,
    workflow_path: pathlib.Path = DEFAULT_WORKFLOW_PATH,
    role_catalog: RoleCatalog | None = None,
    bootstrap_profile: BootstrapProfile | None = None,
) -> DevflowPlan:
    """Build the canonical devflow plan for a repository."""
    resolved_repo_root = repo_root.expanduser().resolve()
    catalog = load_role_catalog() if role_catalog is None else role_catalog
    workflow_payload = _load_workflow_profile(workflow_path.expanduser().resolve())
    workflow_schema = workflow_payload.get("schema_version")
    if workflow_schema != 1:
        raise ValueError(f"unsupported devflow workflow schema version: {workflow_schema!r}")
    stage_order = as_string_list(
        workflow_payload.get("stage_order"), path="devflow workflow.stage_order"
    )
    resolved_bootstrap_profile = (
        resolve_bootstrap_profile(resolved_repo_root)
        if bootstrap_profile is None
        else bootstrap_profile
    )
    normalized_lane = lane.strip() or "default"
    project_id = sha256_hex(resolved_repo_root.as_posix())[:12]
    stages = tuple(_stage_plan(catalog.role(role_name)) for role_name in stage_order)
    resolved_run_id = (
        deterministic_run_id(repo_root=resolved_repo_root, goal=goal, lane=normalized_lane)
        if run_id is None
        else run_id
    )
    return DevflowPlan(
        schema_version=DEVFLOW_PLAN_SCHEMA_VERSION,
        run_id=resolved_run_id,
        goal=goal.strip(),
        repo_root=resolved_repo_root,
        project_id=f"project-{project_id}",
        lane=normalized_lane,
        run_profile=as_string(
            workflow_payload.get("run_profile", catalog.default_run_profile),
            path="devflow workflow.run_profile",
        ),
        bootstrap_profile=resolved_bootstrap_profile.profile_id,
        workflow_path=workflow_path.expanduser().resolve(),
        bootstrap_commands=resolved_bootstrap_profile.commands,
        stages=stages,
    )


def plan_from_dict(payload: dict[str, object]) -> DevflowPlan:
    """Rehydrate a plan from JSON-safe data."""
    mapping = as_mapping(payload, path="devflow plan")
    raw_stages = as_mapping_list(mapping.get("stages", []), path="devflow plan.stages")
    raw_commands = as_mapping(
        mapping.get("bootstrap_commands", {}), path="devflow plan.bootstrap_commands"
    )
    stages: list[DevflowStagePlan] = []
    for raw_stage in raw_stages:
        raw_artifacts = as_mapping_list(
            raw_stage.get("expected_artifacts", []),
            path="devflow plan.stage.expected_artifacts",
        )
        artifacts = tuple(
            RoleArtifact(
                name=as_string(
                    raw_artifact.get("name"), path="devflow plan.stage.expected_artifacts.name"
                ),
                path=pathlib.Path(
                    as_string(
                        raw_artifact.get("path"), path="devflow plan.stage.expected_artifacts.path"
                    )
                ),
                required=as_bool(
                    raw_artifact.get("required", True),
                    path="devflow plan.stage.expected_artifacts.required",
                ),
            )
            for raw_artifact in raw_artifacts
        )
        stages.append(
            DevflowStagePlan(
                name=as_string(raw_stage.get("name"), path="devflow plan.stage.name"),
                role=as_string(raw_stage.get("role"), path="devflow plan.stage.role"),
                backend=as_string(raw_stage.get("backend"), path="devflow plan.stage.backend"),
                permissions_mode=cast(
                    AcpxPermissionMode,
                    as_string(
                        raw_stage.get("permissions_mode"),
                        path="devflow plan.stage.permissions_mode",
                    ),
                ),
                workspace_mode=cast(
                    WorkspaceMode,
                    as_string(
                        raw_stage.get("workspace_mode"), path="devflow plan.stage.workspace_mode"
                    ),
                ),
                retry_budget=as_int(
                    raw_stage.get("retry_budget"), path="devflow plan.stage.retry_budget"
                ),
                expected_artifacts=artifacts,
                approval_required=as_bool(
                    raw_stage.get("approval_required", False),
                    path="devflow plan.stage.approval_required",
                ),
                required_auth_mode=cast(
                    AuthMode,
                    as_string(
                        raw_stage.get("required_auth_mode"),
                        path="devflow plan.stage.required_auth_mode",
                    ),
                ),
                worker_prompt=pathlib.Path(
                    as_string(
                        raw_stage.get("worker_prompt"), path="devflow plan.stage.worker_prompt"
                    )
                )
                .expanduser()
                .resolve(),
            )
        )
    commands: dict[str, tuple[tuple[str, ...], ...]] = {}
    for name, raw_value in raw_commands.items():
        if not isinstance(raw_value, Sequence) or isinstance(raw_value, (str, bytes, bytearray)):
            raise TypeError(f"devflow plan.bootstrap_commands.{name} must be a sequence")
        command_sequence = cast(Sequence[object], raw_value)
        commands[name] = tuple(
            as_string_list(command, path=f"devflow plan.bootstrap_commands.{name}[{index}]")
            for index, command in enumerate(command_sequence)
        )
    return DevflowPlan(
        schema_version=as_int(mapping.get("schema_version"), path="devflow plan.schema_version"),
        run_id=as_string(mapping.get("run_id"), path="devflow plan.run_id"),
        goal=as_string(mapping.get("goal"), path="devflow plan.goal"),
        repo_root=pathlib.Path(as_string(mapping.get("repo_root"), path="devflow plan.repo_root"))
        .expanduser()
        .resolve(),
        project_id=as_string(mapping.get("project_id"), path="devflow plan.project_id"),
        lane=as_string(mapping.get("lane"), path="devflow plan.lane"),
        run_profile=as_string(mapping.get("run_profile"), path="devflow plan.run_profile"),
        bootstrap_profile=as_string(
            mapping.get("bootstrap_profile"), path="devflow plan.bootstrap_profile"
        ),
        workflow_path=pathlib.Path(
            as_string(mapping.get("workflow_path"), path="devflow plan.workflow_path")
        )
        .expanduser()
        .resolve(),
        bootstrap_commands=commands,
        stages=tuple(stages),
    )


def load_devflow_plan(path: pathlib.Path) -> DevflowPlan:
    """Load one serialized plan from JSON."""
    payload = as_mapping(load_json(path), path="devflow plan")
    return plan_from_dict(dict(payload))
