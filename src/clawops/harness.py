"""Simple YAML-driven regression harness."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

from clawops.common import load_yaml, write_text
from clawops.policy_engine import PolicyEngine
from clawops.process_runner import run_command


def _assert_contains(label: str, haystack: str, needles: list[str]) -> list[str]:
    failures: list[str] = []
    for needle in needles:
        if needle not in haystack:
            failures.append(f"{label} missing substring: {needle!r}")
    return failures


def run_command_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one subprocess-based case."""
    command = case["command"]
    timeout = int(case.get("timeout", 30))
    cwd = case.get("cwd")
    shell = bool(case.get("shell", False))
    result = run_command(
        command,
        cwd=cwd,
        timeout_seconds=timeout,
        shell=shell,
    )
    assertions = case.get("assert", {})
    failures: list[str] = []
    if result.timed_out:
        failures.append(f"command timed out after {timeout}s")
    if result.failed_to_start:
        failures.append(f"command failed to start: {result.stderr}")
    if "exit_code" in assertions and result.returncode != assertions["exit_code"]:
        failures.append(f"exit_code expected {assertions['exit_code']} got {result.returncode}")
    failures.extend(
        _assert_contains("stdout", result.stdout, assertions.get("stdout_contains", []))
    )
    failures.extend(
        _assert_contains("stderr", result.stderr, assertions.get("stderr_contains", []))
    )
    for regex in assertions.get("stdout_matches", []):
        if not re.search(regex, result.stdout, re.MULTILINE):
            failures.append(f"stdout missing regex: {regex!r}")
    return {
        "id": case["id"],
        "kind": "command",
        "passed": not failures,
        "failures": failures,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_policy_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one policy-evaluation case."""
    engine = PolicyEngine.from_file(pathlib.Path(case["policy"]))
    payload = json.loads(pathlib.Path(case["input"]).read_text(encoding="utf-8"))
    decision = engine.evaluate(payload)
    expected = case["expect_decision"]
    passed = decision.decision == expected
    return {
        "id": case["id"],
        "kind": "policy",
        "passed": passed,
        "failures": [] if passed else [f"expected {expected!r}, got {decision.decision!r}"],
        "decision": decision.to_dict(),
        "duration_ms": 0,
    }


def run_suite(path: pathlib.Path) -> list[dict[str, Any]]:
    """Run all cases in a YAML suite."""
    suite = load_yaml(path)
    if not isinstance(suite, dict) or "cases" not in suite:
        raise ValueError(f"invalid suite file: {path}")
    results: list[dict[str, Any]] = []
    for case in suite["cases"]:
        kind = case["kind"]
        if kind == "command":
            results.append(run_command_case(case))
            continue
        if kind == "policy":
            results.append(run_policy_case(case))
            continue
        raise ValueError(f"unknown harness case kind: {kind}")
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse harness CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    results = run_suite(args.suite)
    lines = [json.dumps(item, sort_keys=True) for item in results]
    write_text(args.output, "\n".join(lines) + ("\n" if lines else ""))
    passed = sum(1 for item in results if item["passed"])
    print(f"passed={passed} total={len(results)} output={args.output}")
    return 0 if passed == len(results) else 1
