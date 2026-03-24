"""Deterministic workflow runner for operational playbooks."""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from clawops.acp_runner import SessionSpec, run_session
from clawops.app_paths import scoped_state_dir
from clawops.common import canonical_json, load_json, load_yaml, write_json, write_text
from clawops.context_envelope import ContextEnvelopeBuilder, validate_context_envelope
from clawops.context_service import service_from_config
from clawops.op_journal import OperationJournal
from clawops.orchestration import (
    DeliveryTargetDescriptor,
    ProjectDescriptor,
    WorkspaceDescriptor,
    resolve_orchestration_task,
)
from clawops.policy_engine import PolicyEngine
from clawops.process_runner import run_command


@dataclasses.dataclass(slots=True)
class StepResult:
    """Result of a workflow step."""

    name: str
    ok: bool
    message: str
    details: dict[str, object] = dataclasses.field(default_factory=dict)


def _coerce_path(value: object, *, field_name: str) -> pathlib.Path:
    """Validate and normalize a workflow path value."""
    if isinstance(value, pathlib.Path):
        return value
    if isinstance(value, str):
        return pathlib.Path(value)
    raise TypeError(f"{field_name} must be a path string")


def _resolve_base_dir(
    workflow: Mapping[str, Any],
    *,
    workflow_path: pathlib.Path | None,
    cli_base_dir: pathlib.Path | None,
) -> pathlib.Path:
    """Resolve the workflow base directory."""
    if cli_base_dir is not None:
        return cli_base_dir.expanduser().resolve()

    raw_base_dir = workflow.get("base_dir")
    if raw_base_dir is None:
        return pathlib.Path.cwd().resolve()

    base_dir = _coerce_path(raw_base_dir, field_name="workflow.base_dir").expanduser()
    if base_dir.is_absolute():
        return base_dir.resolve()

    anchor = (
        pathlib.Path.cwd() if workflow_path is None else workflow_path.expanduser().resolve().parent
    )
    return (anchor / base_dir).resolve()


def _safe_step_slug(step_name: str) -> str:
    """Return a stable step slug."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", step_name.strip()).strip("-")
    return safe_name or "step"


def _default_context_pack_output(*, base_dir: pathlib.Path, step_name: str) -> pathlib.Path:
    """Return the default on-disk path for a workflow-generated context pack."""
    return scoped_state_dir(base_dir, category="context-packs") / f"{_safe_step_slug(step_name)}.md"


TRUSTED_WORKFLOW_ROOTS: tuple[pathlib.Path, ...] = (
    pathlib.Path(__file__).resolve().parents[2] / "platform/configs/workflows",
)

ALLOWED_WORKFLOW_KINDS = frozenset(
    {
        "shell",
        "policy_check",
        "journal_init",
        "context_pack",
        "worker_dispatch",
        "worker_poll",
        "artifact_gate",
        "approval_gate",
        "workspace_prepare",
        "delivery_prepare",
    }
)


def _validate_optional_positive_int(name: str, value: object) -> None:
    """Validate an optional positive integer workflow field."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_workflow(workflow: object) -> dict[str, Any]:
    """Validate the loaded workflow document."""
    if not isinstance(workflow, dict):
        raise TypeError("workflow must be a mapping")
    base_dir = workflow.get("base_dir")
    if base_dir is not None and not isinstance(base_dir, str):
        raise TypeError("workflow.base_dir must be a string")
    steps = workflow.get("steps", [])
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        raise TypeError("workflow.steps must be a list of mappings")
    for index, step in enumerate(steps, start=1):
        name = step.get("name")
        kind = step.get("kind")
        prefix = f"workflow.steps[{index}]"
        if not isinstance(name, str) or not name:
            raise TypeError(f"{prefix}.name must be a non-empty string")
        if not isinstance(kind, str):
            raise TypeError(f"{prefix}.kind must be a string")
        if kind not in ALLOWED_WORKFLOW_KINDS:
            raise ValueError(
                f"{prefix}.kind must be one of: {', '.join(sorted(ALLOWED_WORKFLOW_KINDS))}"
            )
        if kind == "shell":
            command = step.get("command")
            if isinstance(command, str):
                pass
            elif isinstance(command, list) and all(isinstance(item, str) for item in command):
                pass
            else:
                raise TypeError(f"{prefix}.command must be a string or list of strings")
            shell = step.get("shell")
            if shell is not None and not isinstance(shell, bool):
                raise TypeError(f"{prefix}.shell must be a boolean")
            _validate_optional_positive_int(f"{prefix}.timeout", step.get("timeout"))
            continue
        if kind == "policy_check":
            if not isinstance(step.get("policy"), str):
                raise TypeError(f"{prefix}.policy must be a string")
            if not isinstance(step.get("payload"), dict):
                raise TypeError(f"{prefix}.payload must be a mapping")
            continue
        if kind == "journal_init":
            if not isinstance(step.get("db"), str):
                raise TypeError(f"{prefix}.db must be a string")
            continue
        if kind == "context_pack":
            for field in ("config", "repo", "query"):
                if not isinstance(step.get(field), str):
                    raise TypeError(f"{prefix}.{field} must be a string")
            _validate_optional_positive_int(f"{prefix}.limit", step.get("limit"))
            if step.get("output") is not None and not isinstance(step.get("output"), str):
                raise TypeError(f"{prefix}.output must be a string")
            if step.get("envelope") is not None and not isinstance(step.get("envelope"), bool):
                raise TypeError(f"{prefix}.envelope must be a boolean")
            continue
        if kind == "worker_dispatch":
            if not isinstance(step.get("task"), dict):
                raise TypeError(f"{prefix}.task must be a mapping")
            continue
        if kind == "worker_poll":
            if step.get("dispatch_step") is None and step.get("summary") is None:
                raise TypeError(f"{prefix} must define dispatch_step or summary")
            continue
        if kind == "artifact_gate":
            if step.get("from_step") is None and step.get("artifacts") is None:
                raise TypeError(f"{prefix} must define from_step or artifacts")
            continue
        if kind == "approval_gate":
            if not isinstance(step.get("db"), str):
                raise TypeError(f"{prefix}.db must be a string")
            if step.get("op_id") is None and step.get("from_step") is None:
                raise TypeError(f"{prefix} must define op_id or from_step")
            continue
        if kind == "workspace_prepare":
            if not isinstance(step.get("project"), dict):
                raise TypeError(f"{prefix}.project must be a mapping")
            if not isinstance(step.get("workspace"), dict):
                raise TypeError(f"{prefix}.workspace must be a mapping")
            continue
        if kind == "delivery_prepare":
            if not isinstance(step.get("project"), dict):
                raise TypeError(f"{prefix}.project must be a mapping")
            if not isinstance(step.get("delivery_target"), dict):
                raise TypeError(f"{prefix}.delivery_target must be a mapping")
            continue
    return workflow


def _resolve_workflow_path(path: pathlib.Path, *, allow_untrusted: bool) -> pathlib.Path:
    """Resolve a workflow path and enforce trusted roots by default."""
    resolved = path.expanduser().resolve()
    if allow_untrusted:
        return resolved
    for trusted_root in TRUSTED_WORKFLOW_ROOTS:
        try:
            resolved.relative_to(trusted_root.resolve())
        except ValueError:
            continue
        return resolved
    trusted_roots = ", ".join(str(root.resolve()) for root in TRUSTED_WORKFLOW_ROOTS)
    raise SystemExit(
        f"workflow path {resolved} is outside trusted roots ({trusted_roots}); "
        "pass --allow-untrusted-workflow to override"
    )


class WorkflowRunner:
    """Run a YAML workflow sequentially."""

    def __init__(
        self,
        workflow: dict[str, Any],
        *,
        dry_run: bool = False,
        base_dir: pathlib.Path | None = None,
        workflow_path: pathlib.Path | None = None,
    ) -> None:
        self.workflow = workflow
        self.dry_run = dry_run
        self.base_dir = _resolve_base_dir(
            workflow, workflow_path=workflow_path, cli_base_dir=base_dir
        )
        self.step_state: dict[str, dict[str, object]] = {}

    def _resolve_step_path(self, value: object, *, field_name: str) -> pathlib.Path:
        """Resolve a path-bearing workflow field against the workflow base."""
        path = _coerce_path(value, field_name=field_name).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self.base_dir / path).resolve()

    def _resolve_project(self, step: Mapping[str, Any]) -> ProjectDescriptor:
        """Resolve a project descriptor from one workflow step."""
        payload = step.get("project")
        assert isinstance(payload, dict)
        root = self._resolve_step_path(payload.get("root"), field_name="project.root")
        trusted_roots_raw = payload.get("trusted_roots", [])
        if not isinstance(trusted_roots_raw, list) or not all(
            isinstance(item, str) for item in trusted_roots_raw
        ):
            raise TypeError("project.trusted_roots must be a list of strings")
        trusted_roots = tuple(
            self._resolve_step_path(item, field_name="project.trusted_roots")
            for item in trusted_roots_raw
        )
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("project.metadata must be a mapping")
        return ProjectDescriptor.resolve(
            root,
            project_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            trusted_roots=trusted_roots,
            metadata=metadata,
        )

    def _resolve_workspace(
        self,
        *,
        project: ProjectDescriptor,
        payload: Mapping[str, Any],
    ) -> WorkspaceDescriptor:
        """Resolve a workspace descriptor from one workflow step."""
        path = self._resolve_step_path(payload.get("path"), field_name="workspace.path")
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("workspace.metadata must be a mapping")
        return WorkspaceDescriptor.resolve(
            project,
            kind=str(payload.get("kind")),
            path=path,
            workspace_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            branch=payload.get("branch") if isinstance(payload.get("branch"), str) else None,
            metadata=metadata,
        )

    def _store_step_result(
        self,
        *,
        step_name: str,
        ok: bool,
        message: str,
        details: dict[str, object] | None = None,
    ) -> StepResult:
        """Persist per-step state and return the public result."""
        payload = {} if details is None else details
        self.step_state[step_name] = payload
        return StepResult(step_name, ok, message, payload)

    def _shell_step(self, step: Mapping[str, Any]) -> StepResult:
        """Execute one shell workflow step."""
        if self.dry_run:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=True,
                message=f"dry-run shell: {step['command']}",
            )
        timeout = int(step.get("timeout", 30))
        shell = bool(step.get("shell", False))
        proc = run_command(step["command"], timeout_seconds=timeout, shell=shell)
        if proc.timed_out:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=False,
                message=f"timeout after {timeout}s",
            )
        if proc.failed_to_start:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=False,
                message=f"failed to start: {proc.stderr}",
            )
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=proc.ok,
            message=f"exit={proc.returncode}",
        )

    def _policy_check_step(self, step: Mapping[str, Any]) -> StepResult:
        """Execute one policy check workflow step."""
        policy_path = self._resolve_step_path(step["policy"], field_name="policy_check.policy")
        engine = PolicyEngine.from_file(policy_path)
        decision = engine.evaluate(step["payload"])
        ok = decision.decision in {"allow", "require_approval"}
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=ok,
            message=decision.decision,
            details={"decision": decision.decision},
        )

    def _journal_init_step(self, step: Mapping[str, Any]) -> StepResult:
        """Initialize one operation journal."""
        db_path = self._resolve_step_path(step["db"], field_name="journal_init.db")
        if not self.dry_run:
            journal = OperationJournal(db_path)
            journal.init()
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=True,
            message=f"journal={db_path}",
            details={"db_path": str(db_path)},
        )

    def _context_pack_step(self, step: Mapping[str, Any]) -> StepResult:
        """Build one context pack or context envelope."""
        if self.dry_run:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=True,
                message="dry-run context pack",
            )
        config_path = self._resolve_step_path(step["config"], field_name="context_pack.config")
        repo_path = self._resolve_step_path(step["repo"], field_name="context_pack.repo")
        service = service_from_config(config_path, repo_path)
        service.index()
        if bool(step.get("envelope", False)):
            project = ProjectDescriptor.resolve(repo_path)
            workspace_kind = "git_clone" if (repo_path / ".git").exists() else "local_dir"
            workspace = WorkspaceDescriptor.resolve(project, kind=workspace_kind, path=repo_path)
            output_dir = (
                None
                if step.get("output") is None
                else self._resolve_step_path(step["output"], field_name="context_pack.output")
            )
            builder = ContextEnvelopeBuilder(
                service,
                project=project,
                workspace=workspace,
                lane=str(step.get("lane", "default")),
                role=str(step.get("role", "developer")),
                backend=str(step.get("backend", "codex")),
            )
            envelope = builder.build(
                query=str(step["query"]),
                limit=int(step.get("limit", 8)),
                ttl_seconds=int(step.get("ttl_seconds", 900)),
                output_dir=output_dir,
            )
            validate_context_envelope(envelope, service=service, workspace=workspace)
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=True,
                message=f"context envelope -> {envelope.manifest_path}",
                details={
                    "context_manifest": str(envelope.manifest_path),
                    "context_body": str(envelope.body_path),
                    "reused": envelope.reused,
                },
            )
        output_path = (
            _default_context_pack_output(base_dir=self.base_dir, step_name=str(step["name"]))
            if step.get("output") is None
            else self._resolve_step_path(step["output"], field_name="context_pack.output")
        )
        output = service.pack(str(step["query"]), limit=int(step.get("limit", 8)))
        write_text(output_path, output)
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=True,
            message=f"context packed -> {output_path}",
            details={"output_path": str(output_path)},
        )

    def _workspace_prepare_step(self, step: Mapping[str, Any]) -> StepResult:
        """Resolve and persist one workspace descriptor."""
        project = self._resolve_project(step)
        workspace = self._resolve_workspace(project=project, payload=step["workspace"])
        artifact_path = (
            scoped_state_dir(workspace.working_directory, category="workspace-descriptors")
            / f"{_safe_step_slug(str(step['name']))}.json"
        )
        descriptor = {
            "project_id": project.project_id,
            "workspace_id": workspace.workspace_id,
            "workspace_kind": workspace.kind,
            "project_root": str(project.root),
            "workspace_root": str(workspace.root),
            "working_directory": str(workspace.working_directory),
            "branch": workspace.branch,
        }
        if not self.dry_run:
            write_json(artifact_path, descriptor)
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=True,
            message=f"workspace prepared -> {artifact_path}",
            details={"descriptor_path": str(artifact_path), **descriptor},
        )

    def _delivery_prepare_step(self, step: Mapping[str, Any]) -> StepResult:
        """Resolve and persist one delivery target descriptor."""
        project = self._resolve_project(step)
        payload = step["delivery_target"]
        assert isinstance(payload, dict)
        locator = payload.get("locator", payload.get("path"))
        if not isinstance(locator, str):
            raise TypeError("delivery_target.locator must be a string")
        target = DeliveryTargetDescriptor.resolve(
            project,
            kind=str(payload.get("kind")),
            locator=locator,
            target_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        )
        artifact_path = (
            scoped_state_dir(project.root, category="delivery-targets")
            / f"{_safe_step_slug(str(step['name']))}.json"
        )
        if not self.dry_run:
            write_json(
                artifact_path,
                {
                    "project_id": target.project_id,
                    "target_id": target.target_id,
                    "kind": target.kind,
                    "locator": target.locator,
                },
            )
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=True,
            message=f"delivery target prepared -> {artifact_path}",
            details={
                "descriptor_path": str(artifact_path),
                "project_id": target.project_id,
                "target_id": target.target_id,
                "kind": target.kind,
                "locator": target.locator,
            },
        )

    def _worker_dispatch_step(self, step: Mapping[str, Any]) -> StepResult:
        """Run one ACP-backed worker dispatch step."""
        task = resolve_orchestration_task(step["task"], base_dir=self.base_dir)
        details: dict[str, object] = {
            "task_contract": task.to_contract(),
        }
        if self.dry_run:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=True,
                message=f"dry-run worker dispatch: {task.session_identity}",
                details=details,
            )

        context_manifest: str | None = None
        context_body: str | None = None
        if task.context_request is not None:
            service = service_from_config(
                task.context_request.config_path, task.workspace.working_directory
            )
            builder = ContextEnvelopeBuilder(
                service,
                project=task.project,
                workspace=task.workspace,
                lane=task.lane,
                role=task.role,
                backend=task.backend,
            )
            envelope = builder.build(
                query=task.context_request.query,
                limit=task.context_request.limit,
                ttl_seconds=task.context_request.ttl_seconds,
                prior_artifacts=task.context_request.prior_artifacts,
            )
            validate_context_envelope(envelope, service=service, workspace=task.workspace)
            context_manifest = str(envelope.manifest_path)
            context_body = str(envelope.body_path)
            details["context_manifest"] = context_manifest
            details["context_body"] = context_body

        state_dir = (
            scoped_state_dir(task.workspace.working_directory, category="acp-sessions")
            if step.get("state_dir") is None
            else self._resolve_step_path(step["state_dir"], field_name="worker_dispatch.state_dir")
        )
        journal_db = (
            scoped_state_dir(task.workspace.working_directory, category="workflow-journal")
            / "op_journal.sqlite"
            if step.get("journal_db") is None
            else self._resolve_step_path(
                step["journal_db"], field_name="worker_dispatch.journal_db"
            )
        )
        journal = OperationJournal(journal_db)
        journal.init()
        op = journal.begin(
            scope=task.session_identity,
            kind=task.operation_kind,
            trust_zone=task.role,
            normalized_target=str(task.workspace.working_directory),
            inputs=task.to_contract(),
        )
        details["operation_id"] = op.op_id
        details["journal_db"] = str(journal_db)
        contract_json = canonical_json(task.to_contract())
        if task.approval_required:
            journal.transition(
                op.op_id,
                "pending_approval",
                policy_decision="require_approval",
                execution_contract_version=1,
                execution_contract_json=contract_json,
                approval_required=True,
            )
            approved_by = step.get("approved_by")
            if not isinstance(approved_by, str) or not approved_by.strip():
                return self._store_step_result(
                    step_name=str(step["name"]),
                    ok=False,
                    message=f"approval required before dispatch for {op.op_id}",
                    details=details,
                )
            journal.approve(
                op.op_id,
                approved_by=approved_by,
                note=(
                    step.get("approval_note")
                    if isinstance(step.get("approval_note"), str)
                    else None
                ),
            )
        else:
            journal.transition(
                op.op_id,
                "approved",
                policy_decision="allow",
                execution_contract_version=1,
                execution_contract_json=contract_json,
            )
        journal.transition(op.op_id, "running")

        summary = run_session(
            SessionSpec(
                backend=task.backend,
                prompt=task.prompt,
                project=task.project,
                workspace=task.workspace,
                lane=task.lane,
                role=task.role,
                operation_kind=task.operation_kind,
                state_dir=state_dir,
                timeout_seconds=task.timeout_seconds,
                ttl_seconds=3600,
                required_auth_mode=task.required_auth_mode,
                backend_profile=task.backend_profile,
                journal_db=journal_db,
                session_type=task.role,
                branch=task.workspace.branch,
            )
        )
        terminal_status = "succeeded" if summary.ok else "failed"
        journal.transition(
            op.op_id,
            terminal_status,
            error=None if summary.ok else summary.message,
            result_ok=summary.ok,
            result_status_code=summary.returncode,
            result_body_excerpt=summary.message,
        )
        details["summary_path"] = str(summary.summary_path)
        details["audit_path"] = str(summary.audit_path)
        details["expected_artifacts"] = [
            {
                "name": artifact.name,
                "role": artifact.role,
                "path": str(artifact.path),
                "required": artifact.required,
            }
            for artifact in task.expected_artifacts
        ]
        if context_manifest is not None and context_body is not None:
            details["artifact_refs"] = [context_manifest, context_body, str(summary.audit_path)]
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=summary.ok,
            message=f"{summary.status} -> {summary.summary_path}",
            details=details,
        )

    def _worker_poll_step(self, step: Mapping[str, Any]) -> StepResult:
        """Poll one prior worker dispatch."""
        if self.dry_run:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=True,
                message="dry-run worker poll",
            )
        summary_path: pathlib.Path
        if step.get("dispatch_step") is not None:
            dispatch_step = str(step["dispatch_step"])
            previous = self.step_state.get(dispatch_step, {})
            summary_value = previous.get("summary_path")
            if not isinstance(summary_value, str):
                raise KeyError(f"dispatch step has no summary_path: {dispatch_step}")
            summary_path = pathlib.Path(summary_value)
        else:
            summary_path = self._resolve_step_path(
                step["summary"], field_name="worker_poll.summary"
            )
        payload = load_json(summary_path)
        ok = bool(payload.get("ok"))
        status = str(payload.get("status"))
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=ok,
            message=status,
            details={"summary_path": str(summary_path), "status": status},
        )

    def _artifact_gate_step(self, step: Mapping[str, Any]) -> StepResult:
        """Validate required artifacts before promotion."""
        artifacts: list[tuple[pathlib.Path, bool]] = []
        if step.get("artifacts") is not None:
            raw_artifacts = step["artifacts"]
            if not isinstance(raw_artifacts, list):
                raise TypeError("artifact_gate.artifacts must be a list")
            for index, item in enumerate(raw_artifacts, start=1):
                if isinstance(item, str):
                    artifacts.append(
                        (
                            self._resolve_step_path(
                                item, field_name=f"artifact_gate.artifacts[{index}]"
                            ),
                            True,
                        )
                    )
                    continue
                if not isinstance(item, dict):
                    raise TypeError("artifact_gate.artifacts items must be strings or mappings")
                path = self._resolve_step_path(
                    item.get("path"),
                    field_name=f"artifact_gate.artifacts[{index}].path",
                )
                required = item.get("required", True)
                if not isinstance(required, bool):
                    raise TypeError(f"artifact_gate.artifacts[{index}].required must be a boolean")
                artifacts.append((path, required))
        else:
            from_step = str(step["from_step"])
            previous = self.step_state.get(from_step, {})
            previous_artifacts = previous.get("expected_artifacts", [])
            if not isinstance(previous_artifacts, list):
                raise TypeError("referenced step expected_artifacts must be a list")
            for item in previous_artifacts:
                if not isinstance(item, dict):
                    continue
                path_value = item.get("path")
                required = item.get("required", True)
                if isinstance(path_value, str) and isinstance(required, bool):
                    artifacts.append((pathlib.Path(path_value), required))
        missing = [str(path) for path, required in artifacts if required and not path.exists()]
        ok = not missing
        message = "artifacts ready" if ok else f"missing artifacts: {', '.join(missing)}"
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=ok,
            message=message,
            details={"missing": missing, "artifacts": [str(path) for path, _ in artifacts]},
        )

    def _approval_gate_step(self, step: Mapping[str, Any]) -> StepResult:
        """Require or apply an approval decision in the journal."""
        db_path = self._resolve_step_path(step["db"], field_name="approval_gate.db")
        if self.dry_run:
            return self._store_step_result(
                step_name=str(step["name"]),
                ok=True,
                message=f"dry-run approval gate: {db_path}",
            )
        if step.get("op_id") is not None:
            op_id = str(step["op_id"])
        else:
            previous = self.step_state.get(str(step["from_step"]), {})
            op_value = previous.get("operation_id")
            if not isinstance(op_value, str):
                raise KeyError(f"referenced step has no operation_id: {step['from_step']}")
            op_id = op_value
        journal = OperationJournal(db_path)
        op = journal.get(op_id)
        approved_by = step.get("approved_by")
        if op.status == "pending_approval" and isinstance(approved_by, str) and approved_by.strip():
            op = journal.approve(
                op_id,
                approved_by=approved_by,
                note=step.get("note") if isinstance(step.get("note"), str) else None,
            )
        expected_status = str(step.get("expected_status", "approved"))
        ok = op.status == expected_status
        return self._store_step_result(
            step_name=str(step["name"]),
            ok=ok,
            message=op.status,
            details={"op_id": op_id, "db_path": str(db_path), "status": op.status},
        )

    def run(self) -> list[StepResult]:
        """Execute the workflow."""
        results: list[StepResult] = []
        handlers = {
            "shell": self._shell_step,
            "policy_check": self._policy_check_step,
            "journal_init": self._journal_init_step,
            "context_pack": self._context_pack_step,
            "workspace_prepare": self._workspace_prepare_step,
            "delivery_prepare": self._delivery_prepare_step,
            "worker_dispatch": self._worker_dispatch_step,
            "worker_poll": self._worker_poll_step,
            "artifact_gate": self._artifact_gate_step,
            "approval_gate": self._approval_gate_step,
        }
        for step in self.workflow.get("steps", []):
            kind = str(step["kind"])
            handler = handlers.get(kind)
            if handler is None:
                raise ValueError(f"unknown workflow step kind: {kind}")
            results.append(handler(step))
        return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse workflow CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, type=pathlib.Path)
    parser.add_argument("--base-dir", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-untrusted-workflow", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    workflow_path = _resolve_workflow_path(
        args.workflow,
        allow_untrusted=args.allow_untrusted_workflow,
    )
    workflow = _validate_workflow(load_yaml(workflow_path))
    runner = WorkflowRunner(
        workflow,
        dry_run=args.dry_run,
        base_dir=args.base_dir,
        workflow_path=workflow_path,
    )
    results = runner.run()
    for result in results:
        print(f"{result.name}\t{'ok' if result.ok else 'fail'}\t{result.message}")
    return 0 if all(item.ok for item in results) else 1
