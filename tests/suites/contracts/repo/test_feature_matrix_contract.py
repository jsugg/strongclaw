"""Contracts binding the feature matrix to the product surface, the suite, and the workflow."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import yaml

from clawops.backend_registry import BACKEND_REGISTRY
from clawops.cli import COMMANDS, WRAPPER_COMMANDS
from clawops.memory_profiles import MEMORY_PROFILES
from tests.utils.helpers.feature_verification import FeatureManifest, load_manifest
from tests.utils.helpers.fresh_host import SCENARIO_SPECS
from tests.utils.helpers.repo import REPO_ROOT

_MANIFEST = REPO_ROOT / "tests" / "feature_matrix.yaml"
_SUITE_DIR = REPO_ROOT / "tests" / "suites" / "e2e" / "feature_verification"
_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "feature-verification.yml"
_COMPOSE_DIR = REPO_ROOT / "platform" / "compose"


def _manifest() -> FeatureManifest:
    return load_manifest(_MANIFEST)


def _suite_tests() -> set[str]:
    node_ids: set[str] = set()
    for path in sorted(_SUITE_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                node_ids.add(f"{rel_path}::{node.name}")
    return node_ids


def _cited_files(manifest: FeatureManifest) -> set[str]:
    return {
        call_site.rsplit(":", 1)[0]
        for feature in manifest.features
        for call_site in feature.call_sites
    }


def _module_path(import_path: str) -> str:
    candidate = Path("src", *import_path.split("."))
    return candidate.as_posix() if (REPO_ROOT / candidate).is_dir() else f"{candidate}.py"


def test_every_feature_test_exists_in_the_suite() -> None:
    referenced = set(_manifest().feature_by_test())
    missing = sorted(referenced - _suite_tests())
    assert not missing, f"matrix references tests that do not exist: {missing}"


def test_every_suite_test_maps_to_exactly_one_feature() -> None:
    unmapped = sorted(_suite_tests() - set(_manifest().feature_by_test()))
    assert not unmapped, f"suite tests missing from the matrix: {unmapped}"


def test_every_call_site_resolves_to_a_real_line() -> None:
    for feature in _manifest().features:
        for call_site in feature.call_sites:
            rel_path, line_text = call_site.rsplit(":", 1)
            path = REPO_ROOT / rel_path
            assert path.is_file(), f"{feature.feature_id}: {rel_path} does not exist"
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            assert int(line_text) <= line_count, f"{feature.feature_id}: {call_site} is past EOF"


def test_every_clawops_command_is_covered() -> None:
    cited = _cited_files(_manifest())
    modules = {spec.name: _module_path(spec.import_path) for spec in COMMANDS}
    modules.update(
        {f"wrapper {name}": _module_path(module) for name, (module, _) in WRAPPER_COMMANDS.items()}
    )
    uncovered = sorted(
        command
        for command, module in modules.items()
        if not any(path == module or path.startswith(f"{module}/") for path in cited)
    )
    assert not uncovered, f"clawops commands without a feature: {uncovered}"


def test_every_profile_has_a_profile_feature() -> None:
    feature_ids = {feature.feature_id for feature in _manifest().features}
    missing = sorted(
        profile for profile in MEMORY_PROFILES if f"profile.{profile}" not in feature_ids
    )
    assert not missing, f"profiles without a profile.<id> feature: {missing}"


def test_every_backend_auth_mode_is_covered() -> None:
    feature_ids = {feature.feature_id for feature in _manifest().features}
    missing: list[str] = []
    for name, backend in BACKEND_REGISTRY.items():
        for mode in backend.supported_auth_modes:
            if mode == "local":
                expected = "acp.openclaw-local" if name == "openclaw" else "acp.local-backends"
            else:
                expected = f"acp.{name}-{mode}"
            if expected not in feature_ids:
                missing.append(f"{name}/{mode} -> {expected}")
    assert not missing, f"backend auth modes without a feature: {missing}"


def test_every_shipped_compose_stack_is_covered() -> None:
    cited = _cited_files(_manifest())
    stacks = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in _COMPOSE_DIR.glob("docker-compose.*.yaml")
        if ".ci-hosted-" not in path.name
    )
    uncovered = [stack for stack in stacks if stack not in cited]
    assert not uncovered, f"compose stacks without a feature: {uncovered}"


def test_lanes_match_fresh_host_scenarios() -> None:
    for lane in _manifest().lanes.values():
        spec = SCENARIO_SPECS.get(lane.scenario)  # type: ignore[call-overload]
        assert spec is not None, f"lane {lane.lane_id} uses unknown scenario {lane.scenario}"
        assert spec.platform == lane.platform, lane
        assert spec.activate_services == lane.activate_services, lane
        assert spec.model_setup_mode == "auto", lane


def test_workflow_runs_every_lane_and_nothing_else() -> None:
    workflow = cast(dict[str, object], yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8")))
    jobs = cast(dict[str, dict[str, object]], workflow["jobs"])
    linux_matrix = cast(dict[str, object], jobs["linux-lanes"]["strategy"])["matrix"]
    entries = cast(list[dict[str, str]], cast(dict[str, object], linux_matrix)["include"])
    workflow_lanes = {entry["lane"]: entry for entry in entries}
    workflow_text = _WORKFLOW.read_text(encoding="utf-8")
    manifest = _manifest()
    for lane_id, lane in manifest.lanes.items():
        if lane.platform == "linux":
            assert lane_id in workflow_lanes, f"workflow does not run lane {lane_id}"
            entry = workflow_lanes[lane_id]
            assert (entry["profile"], entry["scenario"]) == (lane.profile, lane.scenario), entry
        else:
            assert f"--lane {lane_id}" in workflow_text, f"workflow does not run lane {lane_id}"
            assert f"--scenario {lane.scenario}" in workflow_text
            assert f"--profile {lane.profile}" in workflow_text
    assert set(workflow_lanes) <= set(manifest.lanes), sorted(
        set(workflow_lanes) - set(manifest.lanes)
    )


def test_unverifiable_features_explain_why() -> None:
    for feature in _manifest().features:
        if feature.status == "unverifiable":
            assert feature.reason is not None and len(feature.reason) >= 40, feature.feature_id
