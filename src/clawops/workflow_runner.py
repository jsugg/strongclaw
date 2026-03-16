"""Deterministic workflow runner for operational playbooks."""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from clawops.common import load_yaml, write_text
from clawops.context_service import service_from_config
from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine
from clawops.process_runner import run_command


@dataclasses.dataclass(slots=True)
class StepResult:
    """Result of a workflow step."""

    name: str
    ok: bool
    message: str


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


def _default_context_pack_output(*, base_dir: pathlib.Path, step_name: str) -> pathlib.Path:
    """Return the default on-disk path for a workflow-generated context pack."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", step_name.strip()).strip("-")
    slug = safe_name or "context-pack"
    return base_dir / ".runs" / "context-packs" / f"{slug}.md"


TRUSTED_WORKFLOW_ROOTS: tuple[pathlib.Path, ...] = (
    pathlib.Path(__file__).resolve().parents[2] / "platform/configs/workflows",
)

ALLOWED_WORKFLOW_KINDS = frozenset({"shell", "policy_check", "journal_init", "context_pack"})


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

    def _resolve_step_path(self, value: object, *, field_name: str) -> pathlib.Path:
        """Resolve a path-bearing workflow field against the workflow base."""
        path = _coerce_path(value, field_name=field_name).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self.base_dir / path).resolve()

    def run(self) -> list[StepResult]:
        """Execute the workflow."""
        results: list[StepResult] = []
        for step in self.workflow.get("steps", []):
            kind = step["kind"]
            name = step["name"]
            if kind == "shell":
                if self.dry_run:
                    results.append(StepResult(name, True, f"dry-run shell: {step['command']}"))
                    continue
                timeout = int(step.get("timeout", 30))
                shell = bool(step.get("shell", False))
                proc = run_command(step["command"], timeout_seconds=timeout, shell=shell)
                if proc.timed_out:
                    results.append(StepResult(name, False, f"timeout after {timeout}s"))
                    continue
                if proc.failed_to_start:
                    results.append(StepResult(name, False, f"failed to start: {proc.stderr}"))
                    continue
                results.append(StepResult(name, proc.ok, f"exit={proc.returncode}"))
                continue
            if kind == "policy_check":
                policy_path = self._resolve_step_path(
                    step["policy"], field_name="policy_check.policy"
                )
                engine = PolicyEngine.from_file(policy_path)
                decision = engine.evaluate(step["payload"])
                ok = decision.decision in {"allow", "require_approval"}
                results.append(StepResult(name, ok, decision.decision))
                continue
            if kind == "journal_init":
                db_path = self._resolve_step_path(step["db"], field_name="journal_init.db")
                journal = OperationJournal(db_path)
                if not self.dry_run:
                    journal.init()
                results.append(StepResult(name, True, f"journal={step['db']}"))
                continue
            if kind == "context_pack":
                if self.dry_run:
                    results.append(StepResult(name, True, "dry-run context pack"))
                    continue
                config_path = self._resolve_step_path(
                    step["config"], field_name="context_pack.config"
                )
                repo_path = self._resolve_step_path(step["repo"], field_name="context_pack.repo")
                service = service_from_config(config_path, repo_path)
                service.index()
                output_path = (
                    _default_context_pack_output(base_dir=self.base_dir, step_name=name)
                    if step.get("output") is None
                    else self._resolve_step_path(step["output"], field_name="context_pack.output")
                )
                output = service.pack(step["query"], limit=int(step.get("limit", 8)))
                write_text(output_path, output)
                results.append(StepResult(name, True, f"context packed -> {output_path}"))
                continue
            raise ValueError(f"unknown workflow step kind: {kind}")
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
