"""Run ACP worker sessions with descriptor-aware locking and durable summaries."""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import pathlib
import shutil
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Final

from clawops.acpx_adapter import (
    AcpxAdapter,
    AcpxInvocation,
    AcpxPermissionMode,
    RequestedOutputFormat,
)
from clawops.app_paths import scoped_state_dir
from clawops.backend_registry import BackendDefinition, resolve_backend
from clawops.common import dump_json, sha256_hex, write_json, write_text
from clawops.credential_broker import CredentialBroker, CredentialStatus
from clawops.op_journal import LeaseConflictError, OperationJournal
from clawops.orchestration import (
    AuthMode,
    DescriptorError,
    ProjectDescriptor,
    WorkspaceDescriptor,
    build_lock_identity,
    build_session_identity,
)

DEFAULT_PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REPO_ROOT: Final[pathlib.Path] = DEFAULT_PROJECT_ROOT


class SessionLockError(RuntimeError):
    """Raised when another ACP session already owns the lock."""


@dataclasses.dataclass(slots=True, frozen=True)
class SessionSpec:
    """Immutable ACP session configuration."""

    backend: str
    prompt: str
    project: ProjectDescriptor
    workspace: WorkspaceDescriptor
    lane: str
    role: str
    operation_kind: str
    state_dir: pathlib.Path
    timeout_seconds: int
    ttl_seconds: int
    required_auth_mode: AuthMode | None = None
    backend_profile: str | None = None
    permissions_mode: AcpxPermissionMode | None = None
    output_format: RequestedOutputFormat = "text"
    config_path: pathlib.Path | None = None
    journal_db: pathlib.Path | None = None
    branch: str | None = None
    session_type: str | None = None

    @property
    def session_identity(self) -> str:
        """Return the canonical session identity."""
        return build_session_identity(
            backend=self.backend,
            project_id=self.project.project_id,
            workspace_id=self.workspace.workspace_id,
            lane=self.lane,
            role=self.role,
        )

    @property
    def lock_identity(self) -> str:
        """Return the canonical lock identity."""
        return build_lock_identity(
            project_id=self.project.project_id,
            workspace_id=self.workspace.workspace_id,
            lane=self.lane,
            role=self.role,
            operation_kind=self.operation_kind,
        )

    @property
    def effective_branch(self) -> str | None:
        """Return the legacy branch token when available."""
        return self.branch or self.workspace.branch

    @property
    def effective_session_type(self) -> str:
        """Return the legacy session-type token."""
        return self.session_type or self.role

    @property
    def effective_journal_db(self) -> pathlib.Path:
        """Return the journal database path for lease tracking."""
        if self.journal_db is not None:
            return self.journal_db
        return self.state_dir / "op_journal.sqlite"


@dataclasses.dataclass(slots=True, frozen=True)
class SessionSummary:
    """Machine-readable ACP session result."""

    ok: bool
    status: str
    message: str
    backend: str
    agent_name: str
    project_id: str
    workspace_id: str
    workspace_kind: str
    lane: str
    role: str
    operation_kind: str
    session_identity: str
    lock_identity: str
    auth_mode: str
    credential_state: str
    credential_source_class: str
    branch: str | None
    session_type: str
    project_root: pathlib.Path
    workspace_root: pathlib.Path
    working_directory: pathlib.Path
    state_dir: pathlib.Path
    journal_db: pathlib.Path
    command: Sequence[str]
    requested_permissions_mode: AcpxPermissionMode | None
    applied_permissions_mode: AcpxPermissionMode | None
    requested_output_format: RequestedOutputFormat
    backend_profile: str | None
    acpx_command: Sequence[str]
    started_at: str
    finished_at: str
    duration_ms: int
    returncode: int | None
    stdout_path: pathlib.Path
    stderr_path: pathlib.Path
    summary_path: pathlib.Path
    audit_path: pathlib.Path
    structured_output_path: pathlib.Path | None
    lease_id: str | None = None
    parsed_output_format: str = "text"
    parsed_event_count: int = 0

    def to_dict(self) -> dict[str, object]:
        """Convert the session summary into JSON-safe primitives."""
        return {
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "backend": self.backend,
            "agent_name": self.agent_name,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "workspace_kind": self.workspace_kind,
            "lane": self.lane,
            "role": self.role,
            "operation_kind": self.operation_kind,
            "session_identity": self.session_identity,
            "lock_identity": self.lock_identity,
            "auth_mode": self.auth_mode,
            "credential_state": self.credential_state,
            "credential_source_class": self.credential_source_class,
            "branch": self.branch,
            "session_type": self.session_type,
            "project_root": str(self.project_root),
            "workspace_root": str(self.workspace_root),
            "working_directory": str(self.working_directory),
            "state_dir": str(self.state_dir),
            "journal_db": str(self.journal_db),
            "command": list(self.command),
            "requested_permissions_mode": self.requested_permissions_mode,
            "applied_permissions_mode": self.applied_permissions_mode,
            "requested_output_format": self.requested_output_format,
            "backend_profile": self.backend_profile,
            "acpx_command": list(self.acpx_command),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "returncode": self.returncode,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "summary_path": str(self.summary_path),
            "audit_path": str(self.audit_path),
            "structured_output_path": (
                None if self.structured_output_path is None else str(self.structured_output_path)
            ),
            "lease_id": self.lease_id,
            "parsed_output_format": self.parsed_output_format,
            "parsed_event_count": self.parsed_event_count,
        }


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _timestamp_token(value: datetime) -> str:
    """Return a stable timestamp token for session directories."""
    return value.strftime("%Y%m%dT%H%M%S%fZ")


def _timestamp_text(value: datetime) -> str:
    """Return an ISO-8601 UTC timestamp."""
    return value.isoformat().replace("+00:00", "Z")


def _default_worktree(project_root: pathlib.Path, branch: str) -> pathlib.Path:
    """Return the legacy default ACP worktree for a branch."""
    return project_root / "repo" / "worktrees" / branch


def _lock_name(lock_identity: str) -> str:
    """Return a deterministic lock filename."""
    return f"{sha256_hex(lock_identity)[:16]}.lock"


def _build_invocation(
    definition: BackendDefinition,
    spec: SessionSpec,
) -> AcpxInvocation:
    """Build the ACPX invocation for one session."""
    return AcpxInvocation(
        agent_name=definition.agent_name,
        prompt=spec.prompt,
        cwd=spec.workspace.working_directory,
        timeout_seconds=spec.timeout_seconds,
        permissions_mode=(
            definition.default_permission_mode
            if spec.permissions_mode is None
            else spec.permissions_mode
        ),
        output_format=spec.output_format,
        backend_profile=spec.backend_profile,
        config_path=spec.config_path,
    )


def _preflight_errors(
    spec: SessionSpec,
    *,
    definition: BackendDefinition,
    credential_status: CredentialStatus,
) -> list[str]:
    """Return a list of blocking ACP session errors."""
    errors: list[str] = []
    if not spec.prompt.strip():
        errors.append("prompt must not be empty")
    if spec.timeout_seconds <= 0:
        errors.append("timeout_seconds must be positive")
    if spec.ttl_seconds <= 0:
        errors.append("ttl_seconds must be positive")
    if shutil.which("acpx") is None:
        errors.append("acpx executable not found in PATH")
    if spec.workspace.kind in {"git_worktree", "git_clone"} and shutil.which("git") is None:
        errors.append("git executable not found in PATH")
    if not spec.project.root.exists():
        errors.append(f"project_root does not exist: {spec.project.root}")
    if not spec.workspace.root.exists():
        errors.append(f"workspace does not exist: {spec.workspace.root}")
    if not spec.workspace.working_directory.is_dir():
        errors.append(f"working directory is not a directory: {spec.workspace.working_directory}")
    if not spec.project.contains(spec.workspace.root):
        errors.append(f"workspace must stay under trusted project roots: {spec.workspace.root}")
    if not definition.supports_auth_mode(credential_status.auth_mode):
        errors.append(
            f"backend {definition.name} does not support auth mode {credential_status.auth_mode}"
        )
    if spec.permissions_mode is not None and not definition.supports_permission_mode(
        spec.permissions_mode
    ):
        errors.append(
            f"backend {definition.name} does not support permissions mode {spec.permissions_mode}"
        )
    if not definition.supports_output_format(spec.output_format):
        errors.append(
            f"backend {definition.name} does not support output format {spec.output_format}"
        )
    if spec.config_path is not None:
        if not spec.config_path.exists():
            errors.append(f"config path does not exist: {spec.config_path}")
        elif not spec.config_path.is_file():
            errors.append(f"config path must be a file: {spec.config_path}")
    if not credential_status.ready:
        errors.append(credential_status.message)
    return errors


@contextmanager
def branch_lock(lock_path: pathlib.Path) -> Generator[None, None, None]:
    """Acquire a non-blocking exclusive session lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SessionLockError(f"session already locked: {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_session_artifacts(
    summary: SessionSummary,
    *,
    stdout_text: str,
    stderr_text: str,
    audit_payload: dict[str, object],
    structured_output: dict[str, object] | None,
) -> None:
    """Write logs, audit records, and summary files for a session."""
    summary.summary_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(summary.stdout_path, stdout_text)
    write_text(summary.stderr_path, stderr_text)
    write_json(summary.summary_path, summary.to_dict())
    write_json(summary.audit_path, audit_payload)
    if structured_output is not None and summary.structured_output_path is not None:
        write_json(summary.structured_output_path, structured_output)


def _base_summary(
    spec: SessionSpec,
    *,
    definition: BackendDefinition,
    credential_status: CredentialStatus,
    invocation: AcpxInvocation | None,
) -> tuple[datetime, SessionSummary]:
    """Create the initial session summary before execution."""
    started = _utc_now()
    session_dir = (
        spec.state_dir
        / spec.project.project_id
        / spec.workspace.workspace_id
        / spec.lane
        / spec.role
        / spec.backend
        / _timestamp_token(started)
    )
    stdout_path = session_dir / "stdout.log"
    stderr_path = session_dir / "stderr.log"
    summary_path = session_dir / "summary.json"
    audit_path = session_dir / "audit.json"
    structured_output_path = session_dir / "structured-output.json"
    command = [] if invocation is None else invocation.command
    summary = SessionSummary(
        ok=False,
        status="pending",
        message="session created",
        backend=spec.backend,
        agent_name=definition.agent_name,
        project_id=spec.project.project_id,
        workspace_id=spec.workspace.workspace_id,
        workspace_kind=spec.workspace.kind,
        lane=spec.lane,
        role=spec.role,
        operation_kind=spec.operation_kind,
        session_identity=spec.session_identity,
        lock_identity=spec.lock_identity,
        auth_mode=credential_status.auth_mode,
        credential_state=credential_status.state,
        credential_source_class=credential_status.source_class,
        branch=spec.effective_branch,
        session_type=spec.effective_session_type,
        project_root=spec.project.root,
        workspace_root=spec.workspace.root,
        working_directory=spec.workspace.working_directory,
        state_dir=spec.state_dir,
        journal_db=spec.effective_journal_db,
        command=command,
        requested_permissions_mode=spec.permissions_mode,
        applied_permissions_mode=None if invocation is None else invocation.permissions_mode,
        requested_output_format=spec.output_format,
        backend_profile=spec.backend_profile,
        acpx_command=command,
        started_at=_timestamp_text(started),
        finished_at=_timestamp_text(started),
        duration_ms=0,
        returncode=None,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        summary_path=summary_path,
        audit_path=audit_path,
        structured_output_path=structured_output_path,
    )
    return started, summary


def run_session(spec: SessionSpec) -> SessionSummary:
    """Execute an ACP session and persist its summary."""
    definition = resolve_backend(spec.backend)
    required_auth_mode = (
        definition.default_auth_mode if spec.required_auth_mode is None else spec.required_auth_mode
    )
    broker = CredentialBroker()
    credential_status = broker.evaluate(spec.backend, required_auth_mode=required_auth_mode)
    errors = _preflight_errors(
        spec,
        definition=definition,
        credential_status=credential_status,
    )
    invocation = None if errors else _build_invocation(definition, spec)
    _, base_summary = _base_summary(
        spec,
        definition=definition,
        credential_status=credential_status,
        invocation=invocation,
    )
    audit_payload: dict[str, object] = {
        "backend": spec.backend,
        "agent_name": definition.agent_name,
        "session_identity": spec.session_identity,
        "lock_identity": spec.lock_identity,
        "auth_mode": credential_status.auth_mode,
        "credential_state": credential_status.state,
        "credential_source_class": credential_status.source_class,
        "readiness_message": credential_status.message,
        "removed_env_keys": list(credential_status.removed_env_keys),
        "journal_db": str(spec.effective_journal_db),
        "requested_permissions_mode": base_summary.requested_permissions_mode,
        "applied_permissions_mode": base_summary.applied_permissions_mode,
        "requested_output_format": base_summary.requested_output_format,
        "backend_profile": base_summary.backend_profile,
        "acpx_command": list(base_summary.acpx_command),
        "config_path": None if spec.config_path is None else str(spec.config_path),
    }

    if errors:
        failed = dataclasses.replace(
            base_summary,
            status="preflight_failed",
            message="; ".join(errors),
            finished_at=_timestamp_text(_utc_now()),
        )
        _write_session_artifacts(
            failed,
            stdout_text="",
            stderr_text=failed.message,
            audit_payload=audit_payload,
            structured_output=None,
        )
        return failed

    journal = OperationJournal(spec.effective_journal_db)
    journal.init()
    lock_path = spec.state_dir / "locks" / _lock_name(spec.lock_identity)
    lease_id: str | None = None
    try:
        with branch_lock(lock_path):
            lease = journal.acquire_lease(
                lock_identity=spec.lock_identity,
                session_identity=spec.session_identity,
                backend=spec.backend,
                project_id=spec.project.project_id,
                workspace_id=spec.workspace.workspace_id,
                lane=spec.lane,
                role=spec.role,
                operation_kind=spec.operation_kind,
                holder=str(spec.workspace.working_directory),
                ttl_seconds=spec.ttl_seconds,
                metadata={
                    "backend_profile": spec.backend_profile,
                    "permissions_mode": base_summary.applied_permissions_mode,
                    "output_format": base_summary.requested_output_format,
                    "workspace_kind": spec.workspace.kind,
                },
            )
            lease_id = lease.lease_id
            audit_payload["lease_id"] = lease_id
            if invocation is None:
                invocation = _build_invocation(definition, spec)
            adapter_result = AcpxAdapter().run(
                invocation,
                env=credential_status.sanitized_env(),
            )
            result = adapter_result.command_result
            parsed_output = adapter_result.parsed_output
            structured_output = parsed_output.to_dict() if parsed_output.format != "text" else None
            finished = _utc_now()
            if result.timed_out:
                summary = dataclasses.replace(
                    base_summary,
                    status="timed_out",
                    message=f"acpx timed out after {spec.timeout_seconds}s",
                    finished_at=_timestamp_text(finished),
                    duration_ms=result.duration_ms,
                    lease_id=lease_id,
                )
            elif result.failed_to_start:
                summary = dataclasses.replace(
                    base_summary,
                    status="failed_to_start",
                    message=result.stderr or "acpx failed to start",
                    finished_at=_timestamp_text(finished),
                    duration_ms=result.duration_ms,
                    lease_id=lease_id,
                )
            elif result.ok:
                summary = dataclasses.replace(
                    base_summary,
                    ok=True,
                    status="succeeded",
                    message="acpx session completed successfully",
                    finished_at=_timestamp_text(finished),
                    duration_ms=result.duration_ms,
                    returncode=result.returncode,
                    lease_id=lease_id,
                    parsed_output_format=parsed_output.format,
                    parsed_event_count=len(parsed_output.events),
                )
            else:
                summary = dataclasses.replace(
                    base_summary,
                    status="failed",
                    message=f"acpx exited with status {result.returncode}",
                    finished_at=_timestamp_text(finished),
                    duration_ms=result.duration_ms,
                    returncode=result.returncode,
                    lease_id=lease_id,
                    parsed_output_format=parsed_output.format,
                    parsed_event_count=len(parsed_output.events),
                )
            released_lease = journal.release_lease(lease_id, released_by=spec.session_identity)
            audit_payload["lease_status"] = released_lease.status
            _write_session_artifacts(
                summary,
                stdout_text=result.stdout,
                stderr_text=result.stderr,
                audit_payload=audit_payload,
                structured_output=structured_output,
            )
            return summary
    except SessionLockError as exc:
        failed = dataclasses.replace(
            base_summary,
            status="lock_conflict",
            message=str(exc),
            finished_at=_timestamp_text(_utc_now()),
        )
        _write_session_artifacts(
            failed,
            stdout_text="",
            stderr_text=failed.message,
            audit_payload=audit_payload,
            structured_output=None,
        )
        return failed
    except LeaseConflictError as exc:
        failed = dataclasses.replace(
            base_summary,
            status="lock_conflict",
            message=str(exc),
            finished_at=_timestamp_text(_utc_now()),
        )
        _write_session_artifacts(
            failed,
            stdout_text="",
            stderr_text=failed.message,
            audit_payload=audit_payload,
            structured_output=None,
        )
        return failed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for ACP sessions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--backend-profile")
    parser.add_argument("--auth-mode")
    parser.add_argument("--branch")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--session-type", default=None)
    parser.add_argument("--role", default=None)
    parser.add_argument("--lane", default="default")
    parser.add_argument("--operation-kind", default="worker_dispatch")
    parser.add_argument(
        "--project-root",
        "--repo-root",
        dest="project_root",
        type=pathlib.Path,
        default=DEFAULT_PROJECT_ROOT,
    )
    parser.add_argument("--project-id")
    parser.add_argument(
        "--workspace", "--worktree", dest="workspace", type=pathlib.Path, default=None
    )
    parser.add_argument("--workspace-kind", default=None)
    parser.add_argument("--workspace-id")
    parser.add_argument("--allowed-workspace-root", action="append", type=pathlib.Path, default=[])
    parser.add_argument("--state-dir", type=pathlib.Path, default=None)
    parser.add_argument("--journal-db", type=pathlib.Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument(
        "--permissions-mode",
        choices=("approve-all", "approve-reads", "deny-all"),
        default=None,
    )
    parser.add_argument("--output-format", choices=("text", "json", "ndjson"), default="text")
    parser.add_argument("--config-path", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def _resolve_session_spec(args: argparse.Namespace) -> SessionSpec:
    """Resolve CLI arguments into a typed session spec."""
    role = args.role or args.session_type or "developer"
    project = ProjectDescriptor.resolve(
        args.project_root,
        project_id=args.project_id,
        trusted_roots=tuple(args.allowed_workspace_root),
    )
    workspace_path = (
        args.workspace
        if args.workspace is not None
        else _default_worktree(project.root, args.branch or "main")
    )
    workspace_kind = args.workspace_kind
    if workspace_kind is None:
        workspace_kind = "git_worktree" if args.branch else "local_dir"
    workspace = WorkspaceDescriptor.resolve(
        project,
        kind=workspace_kind,
        path=workspace_path,
        workspace_id=args.workspace_id,
        branch=args.branch,
    )
    state_dir = (
        args.state_dir.expanduser().resolve()
        if args.state_dir is not None
        else scoped_state_dir(project.root, category="acp-sessions")
    )
    required_auth_mode = args.auth_mode
    if required_auth_mode is not None:
        required_auth_mode = required_auth_mode.strip()
    config_path = None if args.config_path is None else args.config_path.expanduser().resolve()
    return SessionSpec(
        backend=args.backend,
        prompt=args.prompt,
        project=project,
        workspace=workspace,
        lane=args.lane,
        role=role,
        operation_kind=args.operation_kind,
        state_dir=state_dir,
        timeout_seconds=args.timeout_seconds,
        ttl_seconds=args.ttl_seconds,
        required_auth_mode=required_auth_mode,
        backend_profile=args.backend_profile,
        permissions_mode=args.permissions_mode,
        output_format=args.output_format,
        config_path=config_path,
        journal_db=None if args.journal_db is None else args.journal_db.expanduser().resolve(),
        branch=args.branch,
        session_type=args.session_type,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    try:
        spec = _resolve_session_spec(args)
    except (DescriptorError, KeyError, ValueError) as exc:
        print(
            dump_json(
                {
                    "ok": False,
                    "status": "preflight_failed",
                    "message": str(exc),
                    "backend": args.backend,
                    "prompt_sha256": sha256_hex(args.prompt),
                }
            ).rstrip()
        )
        return 1
    summary = run_session(spec)
    print(dump_json(summary.to_dict()).rstrip())
    return 0 if summary.ok else 1
