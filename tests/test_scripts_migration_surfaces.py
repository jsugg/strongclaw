"""Regression checks for the Python-native scripts migration surfaces."""

from __future__ import annotations

import pathlib


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def test_makefile_uses_python_native_operational_targets() -> None:
    makefile = (_repo_root() / "Makefile").read_text(encoding="utf-8")

    assert "preferred_python.sh" not in makefile
    assert "./scripts/" not in makefile
    assert "python -m clawops ops sidecars up" in makefile
    assert "python -m clawops baseline verify" in makefile
    assert "python -m clawops recovery backup-create" in makefile


def test_service_templates_call_repo_venv_python() -> None:
    repo_root = _repo_root()
    gateway = (repo_root / "platform/systemd/openclaw-gateway.service").read_text(encoding="utf-8")
    sidecars = (repo_root / "platform/systemd/openclaw-sidecars.service").read_text(
        encoding="utf-8"
    )
    launchd_gateway = (repo_root / "platform/launchd/ai.openclaw.gateway.plist.template").read_text(
        encoding="utf-8"
    )
    launchd_sidecars = (
        repo_root / "platform/launchd/ai.openclaw.sidecars.plist.template"
    ).read_text(encoding="utf-8")
    launchd_browserlab = (
        repo_root / "platform/launchd/ai.openclaw.browserlab.plist.template"
    ).read_text(encoding="utf-8")

    assert "scripts/ops/" not in gateway
    assert "scripts/ops/" not in sidecars
    assert "__REPO_ROOT__/.venv/bin/python -m clawops" in gateway
    assert "__REPO_ROOT__/.venv/bin/python -m clawops" in sidecars
    assert "__REPO_ROOT__/.venv/bin/python" in launchd_gateway
    assert (
        "Environment=PATH=%h/.config/varlock/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        in gateway
    )
    assert (
        "Environment=PATH=%h/.config/varlock/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        in sidecars
    )
    assert "<key>PATH</key>" in launchd_gateway
    assert "<key>KeepAlive</key>\n    <false/>" in launchd_sidecars
    assert "<string>ops</string>\n      <string>--repo-root</string>" in launchd_gateway
    assert "<string>ops</string>\n      <string>--repo-root</string>" in launchd_sidecars
    assert "<string>ops</string>\n      <string>--repo-root</string>" in launchd_browserlab


def test_ci_workflows_do_not_call_root_scripts_directory() -> None:
    workflow_dir = _repo_root() / ".github" / "workflows"
    for workflow_path in workflow_dir.glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        assert "./scripts/" not in text, workflow_path.as_posix()
