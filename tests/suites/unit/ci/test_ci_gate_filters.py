"""Unit coverage for CI gate path-filter routing."""

from __future__ import annotations

from pathlib import Path

from tests.utils.helpers._ci_workflows.change_router import (
    CiGateSelection,
    evaluate_filter_matches,
    load_ci_gate_filters,
    selection_from_filter_matches,
)
from tests.utils.helpers.repo import REPO_ROOT

_FILTERS_FILE = REPO_ROOT / ".github" / "ci" / "ci-gate-filters.yml"


def _select(*changed_paths: str) -> CiGateSelection:
    filters = load_ci_gate_filters(_FILTERS_FILE)
    matches = evaluate_filter_matches(filters=filters, changed_paths=tuple(changed_paths))
    return selection_from_filter_matches(matches)


def test_docs_only_change_requires_docs_parity_and_skips_heavy_lanes() -> None:
    selection = _select("platform/docs/CI_AND_SECURITY.md")

    assert selection.docs_only is True
    assert selection.any_heavy is False
    assert selection.docs_parity_required is True


def test_docs_plus_docs_parity_contract_change_stays_in_docs_parity_lane() -> None:
    selection = _select("USAGE_GUIDE.md", "tests/suites/contracts/repo/test_docs_parity.py")

    assert selection.docs_only is True
    assert selection.compatibility_matrix is False
    assert selection.security is False
    assert selection.any_heavy is False
    assert selection.docs_parity_required is True


def test_core_runtime_change_routes_to_heavy_lanes() -> None:
    selection = _select("src/clawops/strongclaw_runtime.py")

    assert selection.docs_only is False
    assert selection.fresh_host is True
    assert selection.compatibility_matrix is True
    assert selection.memory_plugin is True
    assert selection.security is True
    assert selection.docs_parity_required is False


def test_memory_plugin_package_change_routes_to_memory_compatibility_and_fresh_host() -> None:
    selection = _select("platform/plugins/memory-lancedb-pro/package.json")

    assert selection.memory_plugin is True
    assert selection.compatibility_matrix is True
    assert selection.fresh_host is True
    assert selection.docs_parity_required is False


def test_security_config_change_routes_to_security_lane() -> None:
    selection = _select("security/semgrep/semgrep.yml")

    assert selection.security is True
    assert selection.harness is False
    assert selection.docs_parity_required is False


def test_ci_gate_filters_file_exists() -> None:
    assert Path(_FILTERS_FILE).is_file()
