"""Devflow operator CLI: plan a run for a scratch project and inspect its state and audit surface.

``devflow run`` dispatches real ACP workers, so it is out of scope here. ``devflow plan`` is a
pure planner that persists nothing, which makes ``status`` and ``audit`` for the planned run id
observable only through their unknown-run contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final, cast

import yaml

from tests.utils.helpers.feature_verification import CommandOutcome, FeatureHost

type JsonObject = dict[str, object]

DEVFLOW_CONFIG_DIR: Final[Path] = Path("platform/configs/devflow")
RUN_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^df_[0-9a-f]{12}$")
GIT_CONFIG_ARGS: Final[tuple[str, ...]] = (
    "-c",
    "user.name=Feature Verification",
    "-c",
    "user.email=featver@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=/dev/null",
)


def _as_object(value: object) -> JsonObject:
    assert isinstance(value, dict), f"expected a JSON object, got {value!r}"
    return cast(JsonObject, value)


def _as_objects(value: object) -> list[JsonObject]:
    assert isinstance(value, list), f"expected a JSON array, got {value!r}"
    return [_as_object(item) for item in cast(list[object], value)]


def _load_yaml(path: Path) -> JsonObject:
    return _as_object(yaml.safe_load(path.read_text(encoding="utf-8")))


def _scratch_project(feature_host: FeatureHost) -> Path:
    project = feature_host.scratch("devflow-project")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "featver-demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (project / "app.py").write_text("def health() -> str:\n    return 'ok'\n", encoding="utf-8")
    for arguments in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "initial commit")):
        feature_host.run(
            ["git", *GIT_CONFIG_ARGS, "-C", str(project), *arguments], timeout=60
        ).require_ok()
    return project


def _devflow(feature_host: FeatureHost, project: Path, *arguments: str) -> CommandOutcome:
    command, *rest = arguments
    return feature_host.clawops("devflow", command, "--project-root", str(project), *rest)


def test_devflow_plan_status_audit(feature_host: FeatureHost) -> None:
    project = _scratch_project(feature_host)
    goal = "Add a /health endpoint"
    plan_outcome = _devflow(feature_host, project, "plan", "--goal", goal).require_ok()
    plan = _as_object(plan_outcome.json())
    run_id = str(plan["run_id"])
    assert RUN_ID_PATTERN.fullmatch(run_id), plan
    assert plan["goal"] == goal and plan["lane"] == "default", plan
    assert plan["repo_root"] == project.resolve().as_posix(), plan
    assert plan["bootstrap_profile"] == "python-basic", plan
    commands = _as_object(plan["bootstrap_commands"])
    assert commands["test_unit"] == [["python", "-m", "pytest", "-q"]], commands

    workflow = _load_yaml(feature_host.repo_root / DEVFLOW_CONFIG_DIR / "workflows/production.yaml")
    roles = _as_object(
        _load_yaml(feature_host.repo_root / DEVFLOW_CONFIG_DIR / "roles.yaml")["roles"]
    )
    assert plan["run_profile"] == workflow["run_profile"], plan
    stages = _as_objects(plan["stages"])
    assert [stage["name"] for stage in stages] == workflow["stage_order"], stages
    for stage in stages:
        role = _as_object(roles[str(stage["role"])])
        assert stage["role"] == stage["name"], stage
        assert stage["backend"] == role["default_backend"], stage
        assert stage["permissions_mode"] == role["permissions_mode"], stage
        assert stage["workspace_mode"] == role["workspace_mode"], stage
        assert stage["required_auth_mode"] == role["required_auth_mode"], stage
        assert stage["approval_required"] is bool(role.get("approval_required", False)), stage
        assert stage["expected_artifacts"] == role["expected_artifacts"], stage
        prompt = Path(str(stage["worker_prompt"]))
        assert prompt.as_posix().endswith(str(role["worker_prompt"])), stage
        assert prompt.is_file(), f"planned worker prompt {prompt} does not exist"
    assert [stage["name"] for stage in stages if stage["approval_required"]] == ["lead"], stages

    replanned = _as_object(
        _devflow(feature_host, project, "plan", "--goal", goal).require_ok().json()
    )
    assert replanned == plan, "planning the same goal must be deterministic"
    other_lane = _as_object(
        _devflow(feature_host, project, "plan", "--goal", goal, "--lane", "hotfix")
        .require_ok()
        .json()
    )
    assert other_lane["run_id"] != run_id and other_lane["lane"] == "hotfix", other_lane
    run_root = project / ".clawops" / "devflow" / run_id
    assert not run_root.exists(), "devflow plan must not materialize a run"

    status = _devflow(feature_host, project, "status", "--run-id", run_id)
    assert status.returncode == 2, status.describe()
    status_payload = _as_object(status.json())
    assert status_payload["ok"] is False and run_id in str(
        status_payload["message"]
    ), status_payload
    stuck = _as_object(
        _devflow(feature_host, project, "status", "--stuck-only").require_ok().json()
    )
    assert stuck == {"stuck_runs": []}, stuck

    audit = _devflow(feature_host, project, "audit", "--run-id", run_id)
    assert not audit.ok, audit.describe()
    assert f"unknown devflow run: {run_id}" in audit.output, audit.describe()
    assert not (run_root / "audit" / "bundle.json").exists(), "no audit bundle for an unknown run"
    assert json.loads(plan_outcome.stdout) == plan, "plan output must be a single JSON document"
