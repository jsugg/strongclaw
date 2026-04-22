"""Governance for the remaining direct monkeypatch exceptions."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT
from tests.utils.scripts.analyze_fixtures import analyze_fixture_tree

_ALLOWED_DIRECT_MONKEYPATCH_FILES = {
    "tests/suites/contracts/taf/test_env_isolation_contract.py",
    "tests/suites/unit/clawops/hypermemory/test_capture.py",
    "tests/suites/unit/clawops/hypermemory/test_providers.py",
    "tests/suites/unit/clawops/test_acp_runner.py",
    "tests/suites/unit/clawops/test_config_cli.py",
    "tests/suites/unit/clawops/test_context_service.py",
    "tests/suites/unit/clawops/test_memory_tools.py",
    "tests/suites/unit/clawops/test_op_journal.py",
    "tests/suites/unit/clawops/test_setup_cli.py",
    "tests/suites/unit/clawops/test_strongclaw_compose.py",
    "tests/suites/unit/clawops/test_strongclaw_ops.py",
    "tests/suites/unit/clawops/test_supply_chain.py",
    "tests/suites/unit/taf/test_identity.py",
    "tests/suites/unit/taf/test_mode_resolution.py",
    "tests/suites/unit/taf/test_test_context.py",
}


def test_direct_monkeypatch_usage_is_allowlisted() -> None:
    report = analyze_fixture_tree(REPO_ROOT)

    assert set(report["direct_monkeypatch_files"]) == _ALLOWED_DIRECT_MONKEYPATCH_FILES
