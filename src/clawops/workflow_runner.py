"""Deterministic workflow runner for operational playbooks."""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import subprocess
from typing import Any

from clawops.common import load_yaml
from clawops.context_service import service_from_config
from clawops.op_journal import OperationJournal
from clawops.policy_engine import PolicyEngine


@dataclasses.dataclass(slots=True)
class StepResult:
    """Result of a workflow step."""

    name: str
    ok: bool
    message: str


class WorkflowRunner:
    """Run a YAML workflow sequentially."""

    def __init__(self, workflow: dict[str, Any], *, dry_run: bool = False) -> None:
        self.workflow = workflow
        self.dry_run = dry_run

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
                proc = subprocess.run(step["command"], shell=isinstance(step["command"], str), check=False)
                results.append(StepResult(name, proc.returncode == 0, f"exit={proc.returncode}"))
                continue
            if kind == "policy_check":
                engine = PolicyEngine.from_file(pathlib.Path(step["policy"]))
                decision = engine.evaluate(step["payload"])
                ok = decision.decision in {"allow", "require_approval"}
                results.append(StepResult(name, ok, decision.decision))
                continue
            if kind == "journal_init":
                journal = OperationJournal(pathlib.Path(step["db"]))
                if not self.dry_run:
                    journal.init()
                results.append(StepResult(name, True, f"journal={step['db']}"))
                continue
            if kind == "context_pack":
                if self.dry_run:
                    results.append(StepResult(name, True, "dry-run context pack"))
                    continue
                service = service_from_config(pathlib.Path(step["config"]), pathlib.Path(step["repo"]))
                service.index()
                _ = service.pack(step["query"], limit=int(step.get("limit", 8)))
                results.append(StepResult(name, True, "context packed"))
                continue
            raise ValueError(f"unknown workflow step kind: {kind}")
        return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse workflow CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    workflow = load_yaml(args.workflow)
    if not isinstance(workflow, dict):
        raise TypeError("workflow must be a mapping")
    runner = WorkflowRunner(workflow, dry_run=args.dry_run)
    results = runner.run()
    for result in results:
        print(f"{result.name}\t{'ok' if result.ok else 'fail'}\t{result.message}")
    return 0 if all(item.ok for item in results) else 1
