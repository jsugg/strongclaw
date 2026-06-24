"""Regression tests for the ci_gate CLI dispatch."""

from __future__ import annotations

from pathlib import Path

from tests.plugins.infrastructure.context import TestContext


def test_ci_gate_main_dispatches_run_docs_parity(
    test_context: TestContext,
    tmp_path: Path,
) -> None:
    """`run-docs-parity` must dispatch without reading absent lane flags.

    Regression: ``main`` previously built the lane selection unconditionally,
    so ``run-docs-parity`` (whose subparser registers only ``--repo-root``)
    crashed with ``AttributeError: 'Namespace' object has no attribute
    'docs_only'`` before ever reaching its handler.
    """
    from tests.scripts import ci_gate as ci_gate_script

    seen_commands: list[list[str]] = []

    def fake_run_checked(command: list[str], *, cwd: Path) -> None:
        seen_commands.append(command)

    test_context.patch.patch_object(ci_gate_script, "run_checked", new=fake_run_checked)

    exit_code = ci_gate_script.main(["run-docs-parity", "--repo-root", str(tmp_path)])

    assert exit_code == 0
    assert ["uv", "sync", "--locked"] in seen_commands
    assert [
        "uv",
        "run",
        "pytest",
        "-q",
        "tests/suites/contracts/repo/test_docs_parity.py",
    ] in seen_commands
