"""Public devflow CLI surface for Strongclaw."""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
from typing import Any, cast

from clawops.common import (
    canonical_json,
    dump_json,
    load_json,
    load_text,
    sha256_hex,
    utc_now_ms,
    write_json,
    write_yaml,
)
from clawops.devflow_contract import (
    DevflowPlan,
    DevflowStagePlan,
    build_devflow_plan,
    devflow_run_root,
    load_devflow_plan,
)
from clawops.devflow_state import (
    DevflowRunRecord,
    DevflowRunView,
    begin_run,
    cancel_run,
    get_run,
    list_stuck_runs,
    mark_run_succeeded,
    record_stage_completed,
    record_stage_failed,
    record_stage_started,
    resume_run,
)
from clawops.devflow_workspaces import DevflowWorkspacePlanner
from clawops.root_detection import resolve_strongclaw_repo_root
from clawops.workflow_runner import WorkflowRunner


def _default_requested_by() -> str:
    """Return the default operator identity for devflow commands."""
    return os.environ.get("USER", "operator")


def _repo_root(path: str | pathlib.Path | None) -> pathlib.Path:
    """Resolve one repository root argument."""
    return resolve_strongclaw_repo_root(path)


def _journal_db(repo_root: pathlib.Path) -> pathlib.Path:
    """Return the canonical repository journal path."""
    return repo_root / ".clawops" / "op_journal.sqlite"


def _run_directories(run_root: pathlib.Path, plan: DevflowPlan) -> None:
    """Create the canonical on-disk run layout."""
    (run_root / "artifacts").mkdir(parents=True, exist_ok=True)
    (run_root / "audit").mkdir(parents=True, exist_ok=True)
    (run_root / "logs").mkdir(parents=True, exist_ok=True)
    (run_root / "sessions").mkdir(parents=True, exist_ok=True)
    (run_root / "summaries").mkdir(parents=True, exist_ok=True)
    (run_root / "workspaces").mkdir(parents=True, exist_ok=True)
    for stage in plan.stages:
        (run_root / "artifacts" / stage.name).mkdir(parents=True, exist_ok=True)


def _manifest_path(run_root: pathlib.Path) -> pathlib.Path:
    """Return the run-level artifact manifest path."""
    return run_root / "artifacts" / "manifest.json"


def _write_log(run_root: pathlib.Path, event: str, payload: dict[str, object]) -> None:
    """Append one devflow lifecycle event to the run log."""
    record = {"event": event, "timestamp_ms": utc_now_ms(), **payload}
    log_path = run_root / "logs" / "devflow.log.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")


def _summary_target(run_root: pathlib.Path, stage_name: str) -> pathlib.Path:
    """Return the canonical summary target for a stage."""
    return run_root / "summaries" / f"{stage_name}.summary.json"


def _audit_target(run_root: pathlib.Path, stage_name: str) -> pathlib.Path:
    """Return the canonical audit target for a stage."""
    return run_root / "audit" / f"{stage_name}.audit.json"


def _copy_optional(source_path: str | None, destination: pathlib.Path) -> pathlib.Path | None:
    """Copy a JSON artifact when the source path exists."""
    if source_path is None:
        return None
    source = pathlib.Path(source_path).expanduser().resolve()
    if not source.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _workspace_kind(path: pathlib.Path) -> str:
    """Return the orchestration workspace kind for a path."""
    return "git_worktree" if (path / ".git").exists() else "local_dir"


def _render_stage_prompt(plan: DevflowPlan, stage: DevflowStagePlan) -> str:
    """Render the stage prompt sent to the ACP worker."""
    prompt_body = load_text(stage.worker_prompt).strip()
    artifact_lines = "\n".join(
        f"- {artifact.path.as_posix()}" for artifact in stage.expected_artifacts
    )
    return (
        f"{prompt_body}\n\n"
        f"Goal:\n{plan.goal}\n\n"
        f"Run:\n"
        f"- run_id: {plan.run_id}\n"
        f"- repo_root: {plan.repo_root.as_posix()}\n"
        f"- lane: {plan.lane}\n"
        f"- stage: {stage.name}\n\n"
        "Expected artifacts:\n"
        f"{artifact_lines or '- none'}\n"
    )


def _task_expected_artifacts(
    run_root: pathlib.Path, stage: DevflowStagePlan
) -> list[dict[str, object]]:
    """Return task artifact descriptors rooted at the run directory."""
    return [
        {
            "name": artifact.name,
            "role": stage.role,
            "path": str(run_root / artifact.path),
            "required": artifact.required,
        }
        for artifact in stage.expected_artifacts
    ]


def _artifact_gate_step(stage: DevflowStagePlan) -> dict[str, object]:
    """Return one workflow artifact-gate step."""
    return {
        "name": f"{stage.name}-artifact-gate",
        "kind": "artifact_gate",
        "from_step": f"{stage.name}-worker-dispatch",
    }


def _artifact_manifest_step(
    stage: DevflowStagePlan, run_root: pathlib.Path, run_id: str
) -> dict[str, object]:
    """Return one workflow artifact-manifest step."""
    return {
        "name": f"{stage.name}-artifact-manifest",
        "kind": "artifact_manifest",
        "run_root": str(run_root),
        "manifest": str(_manifest_path(run_root)),
        "run_id": run_id,
        "stage_name": stage.name,
        "role": stage.role,
        "artifacts": [
            {
                "name": artifact.name,
                "path": artifact.path.as_posix(),
                "required": artifact.required,
            }
            for artifact in stage.expected_artifacts
        ],
    }


def _compile_stage_workflow(
    *,
    plan: DevflowPlan,
    stage: DevflowStagePlan,
    run_root: pathlib.Path,
    workspace_root: pathlib.Path,
    approved_by: str | None,
) -> dict[str, object]:
    """Compile one concrete stage workflow from the devflow plan."""
    steps: list[dict[str, object]] = [
        {
            "name": f"{stage.name}-stage-start",
            "kind": "stage_record",
            "db": str(_journal_db(plan.repo_root)),
            "run_id": plan.run_id,
            "stage_name": stage.name,
            "stage_index": next(
                index for index, item in enumerate(plan.stages) if item.name == stage.name
            ),
            "role": stage.role,
            "workspace_root": str(workspace_root),
            "status": "running",
            "retry_budget": stage.retry_budget,
        }
    ]
    if stage.workspace_mode in {"verify_only", "read_only"}:
        steps.append(
            {
                "name": f"{stage.name}-git-snapshot",
                "kind": "git_snapshot",
                "workspace": str(workspace_root),
                "output": str(run_root / "logs" / f"{stage.name}.before.json"),
            }
        )
    worker_dispatch: dict[str, object] = {
        "name": f"{stage.name}-worker-dispatch",
        "kind": "worker_dispatch",
        "state_dir": str(run_root / "sessions" / stage.name),
        "journal_db": str(_journal_db(plan.repo_root)),
        "task": {
            "project": {"root": str(plan.repo_root)},
            "workspace": {"kind": _workspace_kind(workspace_root), "path": str(workspace_root)},
            "lane": plan.lane,
            "role": stage.role,
            "backend": stage.backend,
            "prompt": _render_stage_prompt(plan, stage),
            "operation_kind": f"devflow-{stage.name}",
            "required_auth_mode": stage.required_auth_mode,
            "permissions_mode": stage.permissions_mode,
            "workspace_mode": stage.workspace_mode,
            "approval_required": stage.approval_required,
            "retry_budget": stage.retry_budget,
            "artifact_contract_id": f"{plan.run_id}:{stage.name}",
            "expected_artifacts": _task_expected_artifacts(run_root, stage),
        },
    }
    if stage.approval_required and approved_by is not None:
        worker_dispatch["approved_by"] = approved_by
    steps.append(worker_dispatch)
    steps.append(_artifact_gate_step(stage))
    if stage.workspace_mode in {"verify_only", "read_only"}:
        steps.append(
            {
                "name": f"{stage.name}-git-mutation-gate",
                "kind": "git_mutation_gate",
                "workspace": str(workspace_root),
                "from_step": f"{stage.name}-git-snapshot",
            }
        )
    steps.append(_artifact_manifest_step(stage, run_root, plan.run_id))
    return {
        "schema_version": 1,
        "name": f"devflow-{plan.run_id}-{stage.name}",
        "base_dir": str(plan.repo_root),
        "stop_on_failure": False,
        "steps": steps,
    }


def _write_run_snapshot(run_root: pathlib.Path, view: DevflowRunView) -> None:
    """Persist the current run view to ``run.json``."""
    write_json(run_root / "run.json", view.to_dict())


def _stage_source_root(plan: DevflowPlan, view: DevflowRunView, start_index: int) -> pathlib.Path:
    """Return the source root to use when executing or resuming from *start_index*."""
    current_root = plan.repo_root
    stage_by_name = {stage.name: stage for stage in plan.stages}
    for record in view.stages:
        stage_plan = stage_by_name.get(record.stage_name)
        if stage_plan is None:
            continue
        if record.stage_index >= start_index:
            break
        if record.status == "succeeded" and stage_plan.workspace_mode in {
            "mutable_primary",
            "mutable_test",
        }:
            current_root = pathlib.Path(record.workspace_root).expanduser().resolve()
    failed_stage = view.next_incomplete_stage()
    if (
        failed_stage is not None
        and failed_stage.stage_index == start_index
        and failed_stage.workspace_root
        and stage_by_name[failed_stage.stage_name].workspace_mode
        in {"mutable_primary", "mutable_test"}
    ):
        current_root = pathlib.Path(failed_stage.workspace_root).expanduser().resolve()
    return current_root


def _execute_plan(
    *,
    plan: DevflowPlan,
    run_root: pathlib.Path,
    approved_by: str | None,
    start_index: int = 0,
) -> dict[str, object]:
    """Execute the devflow plan from *start_index* onward."""
    planner = DevflowWorkspacePlanner(repo_root=plan.repo_root, run_root=run_root)
    view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
    current_source_root = _stage_source_root(plan, view, start_index)
    workflow_stages: list[dict[str, object]] = []
    workflow_contract: dict[str, object] = {
        "schema_version": 1,
        "run_id": plan.run_id,
        "repo_root": plan.repo_root.as_posix(),
        "stages": workflow_stages,
    }
    for stage_index, stage in enumerate(plan.stages[start_index:], start=start_index):
        planned_root = planner.planned_root(
            stage_name=stage.name,
            workspace_mode=stage.workspace_mode,
        )
        try:
            workspace = planner.prepare(
                stage_name=stage.name,
                workspace_mode=stage.workspace_mode,
                source_root=current_source_root,
            )
            stage_workflow = _compile_stage_workflow(
                plan=plan,
                stage=stage,
                run_root=run_root,
                workspace_root=workspace.root,
                approved_by=approved_by,
            )
            workflow_stages.append(
                {
                    "name": stage.name,
                    "workspace_mode": stage.workspace_mode,
                    "workspace_root": workspace.root.as_posix(),
                    "workflow": stage_workflow,
                }
            )
            write_yaml(run_root / "workflow.yaml", workflow_contract)
            _write_log(
                run_root,
                "stage_prepare",
                {
                    "run_id": plan.run_id,
                    "stage": stage.name,
                    "workspace_root": workspace.root.as_posix(),
                },
            )
            results = WorkflowRunner(stage_workflow, base_dir=plan.repo_root).run()
            dispatch_result = next(
                result for result in results if result.name == f"{stage.name}-worker-dispatch"
            )
            manifest_result = next(
                result for result in results if result.name == f"{stage.name}-artifact-manifest"
            )
            summary_path = _copy_optional(
                cast(str | None, dispatch_result.details.get("summary_path")),
                _summary_target(run_root, stage.name),
            )
            audit_path = _copy_optional(
                cast(str | None, dispatch_result.details.get("audit_path")),
                _audit_target(run_root, stage.name),
            )
            manifest_path = (
                pathlib.Path(str(manifest_result.details["manifest_path"])).expanduser().resolve()
            )
            failed_messages = [result.message for result in results if not result.ok]
            if failed_messages:
                record_stage_failed(
                    _journal_db(plan.repo_root),
                    run_id=plan.run_id,
                    stage_name=stage.name,
                    summary_path=summary_path,
                    audit_path=audit_path,
                    artifact_manifest_path=manifest_path,
                    reason="; ".join(failed_messages),
                )
                view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
                _write_run_snapshot(run_root, view)
                _write_log(
                    run_root,
                    "stage_failed",
                    {"run_id": plan.run_id, "stage": stage.name, "messages": failed_messages},
                )
                return {
                    "ok": False,
                    "run_id": plan.run_id,
                    "stage": stage.name,
                    "messages": failed_messages,
                }
            record_stage_completed(
                _journal_db(plan.repo_root),
                run_id=plan.run_id,
                stage_name=stage.name,
                summary_path=summary_path,
                audit_path=audit_path,
                artifact_manifest_path=manifest_path,
            )
            if stage.workspace_mode in {"mutable_primary", "mutable_test"}:
                current_source_root = workspace.root
            view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
            _write_run_snapshot(run_root, view)
            _write_log(
                run_root,
                "stage_succeeded",
                {
                    "run_id": plan.run_id,
                    "stage": stage.name,
                    "workspace_root": workspace.root.as_posix(),
                },
            )
        except Exception as exc:
            view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
            stage_record = next(
                (record for record in view.stages if record.stage_name == stage.name),
                None,
            )
            if stage_record is None or stage_record.status != "running":
                record_stage_started(
                    _journal_db(plan.repo_root),
                    run_id=plan.run_id,
                    stage_name=stage.name,
                    stage_index=stage_index,
                    role=stage.role,
                    workspace_root=planned_root,
                    retry_budget=stage.retry_budget,
                )
            record_stage_failed(
                _journal_db(plan.repo_root),
                run_id=plan.run_id,
                stage_name=stage.name,
                reason=str(exc),
            )
            view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
            _write_run_snapshot(run_root, view)
            _write_log(
                run_root,
                "stage_failed",
                {"run_id": plan.run_id, "stage": stage.name, "messages": [str(exc)]},
            )
            return {
                "ok": False,
                "run_id": plan.run_id,
                "stage": stage.name,
                "messages": [str(exc)],
            }
    final_view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
    run_record = mark_run_succeeded(
        _journal_db(plan.repo_root),
        run_id=plan.run_id,
        summary={
            "completed_stages": [
                stage.stage_name for stage in final_view.stages if stage.status == "succeeded"
            ],
            "manifest_path": _manifest_path(run_root).as_posix(),
        },
    )
    final_view = get_run(_journal_db(plan.repo_root), run_id=plan.run_id)
    _write_run_snapshot(run_root, final_view)
    _write_log(
        run_root,
        "run_succeeded",
        {"run_id": run_record.run_id, "completed_stage_count": len(final_view.stages)},
    )
    return {
        "ok": True,
        "run_id": run_record.run_id,
        "status": run_record.status,
        "completed_stage_count": len(final_view.stages),
    }


def _status_payload(view: DevflowRunView) -> dict[str, object]:
    """Return the JSON-safe public status payload."""
    payload = view.to_dict()
    payload["artifact_manifest_path"] = _manifest_path(
        devflow_run_root(pathlib.Path(view.run.repo_root), view.run.run_id)
    ).as_posix()
    return payload


def _audit_bundle(run_root: pathlib.Path, view: DevflowRunView) -> pathlib.Path:
    """Build the audit bundle JSON for one run."""
    manifest_path = _manifest_path(run_root)
    payload: dict[str, Any] = view.to_dict()
    payload["artifact_manifest"] = load_json(manifest_path) if manifest_path.exists() else None
    payload["summary_files"] = {}
    for stage in view.stages:
        if stage.summary_path is not None:
            summary_path = pathlib.Path(stage.summary_path)
            if summary_path.exists():
                payload["summary_files"][stage.stage_name] = load_json(summary_path)
    bundle_path = run_root / "audit" / "bundle.json"
    write_json(bundle_path, payload)
    return bundle_path


def _save_plan(run_root: pathlib.Path, plan: DevflowPlan) -> None:
    """Persist ``plan.json`` under the run root."""
    write_json(run_root / "plan.json", plan.to_dict())


def _write_initial_run_view(run_root: pathlib.Path, record: DevflowRunRecord) -> None:
    """Write the initial run snapshot before any stages execute."""
    write_json(
        run_root / "run.json",
        {
            "run": record.to_dict(),
            "stages": [],
            "events": [],
        },
    )


def _handle_plan(args: argparse.Namespace) -> int:
    """Implement ``clawops devflow plan``."""
    plan = build_devflow_plan(
        repo_root=_repo_root(args.repo_root),
        goal=args.goal,
        lane=args.lane,
        run_id=args.run_id,
    )
    print(dump_json(plan.to_dict()), end="")
    return 0


def _handle_run(args: argparse.Namespace) -> int:
    """Implement ``clawops devflow run``."""
    plan = build_devflow_plan(
        repo_root=_repo_root(args.repo_root),
        goal=args.goal,
        lane=args.lane,
        run_id=args.run_id,
    )
    run_root = devflow_run_root(plan.repo_root, plan.run_id)
    if run_root.exists():
        print(
            dump_json(
                {
                    "ok": False,
                    "message": f"devflow run already exists: {plan.run_id}",
                    "run_root": run_root.as_posix(),
                }
            ),
            end="",
        )
        return 2
    _run_directories(run_root, plan)
    _save_plan(run_root, plan)
    record = begin_run(
        _journal_db(plan.repo_root),
        run_id=plan.run_id,
        repo_root=plan.repo_root,
        project_id=plan.project_id,
        workspace_id=f"workspace-{sha256_hex(plan.repo_root.as_posix())[:12]}",
        lane=plan.lane,
        goal=plan.goal,
        run_profile=plan.run_profile,
        bootstrap_profile=plan.bootstrap_profile,
        workflow_path=plan.workflow_path,
        plan_sha256=sha256_hex(canonical_json(plan.to_dict())),
        requested_by=args.requested_by,
        summary={"bootstrap_commands": list(plan.bootstrap_commands)},
    )
    _write_initial_run_view(run_root, record)
    _write_log(run_root, "run_started", {"run_id": plan.run_id, "requested_by": args.requested_by})
    result = _execute_plan(
        plan=plan,
        run_root=run_root,
        approved_by=args.approved_by,
        start_index=0,
    )
    print(dump_json(result), end="")
    return 0 if bool(result.get("ok")) else 1


def _handle_status(args: argparse.Namespace) -> int:
    """Implement ``clawops devflow status``."""
    repo_root = _repo_root(args.repo_root)
    if args.stuck_only:
        stuck_runs = [
            run.to_dict()
            for run in list_stuck_runs(_journal_db(repo_root), older_than_ms=args.older_than_ms)
        ]
        print(dump_json({"stuck_runs": stuck_runs}), end="")
        return 0
    if not args.run_id:
        raise SystemExit("status requires --run-id unless --stuck-only is set")
    try:
        view = get_run(_journal_db(repo_root), run_id=args.run_id)
    except KeyError as exc:
        print(dump_json({"ok": False, "message": str(exc)}), end="")
        return 2
    print(dump_json(_status_payload(view)), end="")
    return 0


def _handle_resume(args: argparse.Namespace) -> int:
    """Implement ``clawops devflow resume``."""
    repo_root = _repo_root(args.repo_root)
    run_root = devflow_run_root(repo_root, args.run_id)
    plan = load_devflow_plan(run_root / "plan.json")
    view = resume_run(_journal_db(repo_root), run_id=args.run_id)
    next_stage = view.next_incomplete_stage()
    if next_stage is None:
        print(
            dump_json({"ok": False, "message": f"run {args.run_id} has no incomplete stages"}),
            end="",
        )
        return 2
    result = _execute_plan(
        plan=plan,
        run_root=run_root,
        approved_by=args.approved_by,
        start_index=next_stage.stage_index,
    )
    print(dump_json(result), end="")
    return 0 if bool(result.get("ok")) else 1


def _handle_cancel(args: argparse.Namespace) -> int:
    """Implement ``clawops devflow cancel``."""
    repo_root = _repo_root(args.repo_root)
    run_root = devflow_run_root(repo_root, args.run_id)
    try:
        record = cancel_run(
            _journal_db(repo_root), run_id=args.run_id, requested_by=args.requested_by
        )
    except (KeyError, ValueError) as exc:
        print(dump_json({"ok": False, "message": str(exc)}), end="")
        return 2
    _write_run_snapshot(run_root, get_run(_journal_db(repo_root), run_id=args.run_id))
    print(dump_json({"ok": True, "run": record.to_dict()}), end="")
    return 0


def _handle_audit(args: argparse.Namespace) -> int:
    """Implement ``clawops devflow audit``."""
    repo_root = _repo_root(args.repo_root)
    run_root = devflow_run_root(repo_root, args.run_id)
    view = get_run(_journal_db(repo_root), run_id=args.run_id)
    bundle_path = _audit_bundle(run_root, view)
    print(dump_json({"ok": True, "bundle_path": bundle_path.as_posix()}), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the devflow CLI parser."""
    parser = argparse.ArgumentParser(
        prog="clawops devflow", description="Run Strongclaw devflow orchestration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_common_flags(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--repo-root",
            default=None,
            help="Repository root for devflow state and planning.",
        )
        target.add_argument("--lane", default="default", help="Lane identifier for the run.")

    plan_parser = subparsers.add_parser("plan", help="Render the devflow plan JSON.")
    _add_common_flags(plan_parser)
    plan_parser.add_argument("--goal", required=True, help="Operator goal for the run.")
    plan_parser.add_argument("--run-id", help="Explicit run identifier override.")
    plan_parser.set_defaults(handler=_handle_plan)

    run_parser = subparsers.add_parser("run", help="Create and execute a devflow run.")
    _add_common_flags(run_parser)
    run_parser.add_argument("--goal", required=True, help="Operator goal for the run.")
    run_parser.add_argument("--run-id", help="Explicit run identifier override.")
    run_parser.add_argument(
        "--requested-by",
        default=_default_requested_by(),
        help="Operator identity recorded in run state.",
    )
    run_parser.add_argument(
        "--approved-by", help="Approval identity used for approval-gated stages."
    )
    run_parser.set_defaults(handler=_handle_run)

    status_parser = subparsers.add_parser("status", help="Inspect devflow run state.")
    _add_common_flags(status_parser)
    status_parser.add_argument("--run-id", help="Run identifier to inspect.")
    status_parser.add_argument(
        "--stuck-only", action="store_true", help="List stale non-terminal runs instead of one run."
    )
    status_parser.add_argument(
        "--older-than-ms",
        type=int,
        default=3_600_000,
        help="Staleness threshold used with --stuck-only.",
    )
    status_parser.set_defaults(handler=_handle_status)

    resume_parser = subparsers.add_parser(
        "resume", help="Resume a failed or incomplete devflow run."
    )
    _add_common_flags(resume_parser)
    resume_parser.add_argument("--run-id", required=True, help="Run identifier to resume.")
    resume_parser.add_argument(
        "--approved-by", help="Approval identity used for approval-gated stages."
    )
    resume_parser.set_defaults(handler=_handle_resume)

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a non-terminal devflow run.")
    _add_common_flags(cancel_parser)
    cancel_parser.add_argument("--run-id", required=True, help="Run identifier to cancel.")
    cancel_parser.add_argument(
        "--requested-by",
        default=_default_requested_by(),
        help="Operator identity recorded in the cancel event.",
    )
    cancel_parser.set_defaults(handler=_handle_cancel)

    audit_parser = subparsers.add_parser("audit", help="Build the audit bundle for one run.")
    _add_common_flags(audit_parser)
    audit_parser.add_argument("--run-id", required=True, help="Run identifier to audit.")
    audit_parser.set_defaults(handler=_handle_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch the devflow subcommands."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
