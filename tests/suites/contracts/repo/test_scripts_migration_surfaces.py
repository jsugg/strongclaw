"""Regression checks for the Python-native scripts migration surfaces."""

from __future__ import annotations

from tests.fixtures.repo import REPO_ROOT


def test_makefile_uses_python_native_operational_targets() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "preferred_python.sh" not in makefile
    assert "./scripts/" not in makefile
    assert "PYTHONPATH=src" not in makefile
    assert "$(RUN) clawops ops sidecars up" in makefile
    assert "$(RUN) clawops baseline verify" in makefile
    assert "$(RUN) clawops recovery backup-create" in makefile


def test_service_templates_call_repo_venv_python() -> None:
    gateway = (REPO_ROOT / "platform/systemd/openclaw-gateway.service").read_text(encoding="utf-8")
    sidecars = (REPO_ROOT / "platform/systemd/openclaw-sidecars.service").read_text(
        encoding="utf-8"
    )
    launchd_gateway = (REPO_ROOT / "platform/launchd/ai.openclaw.gateway.plist.template").read_text(
        encoding="utf-8"
    )
    launchd_sidecars = (
        REPO_ROOT / "platform/launchd/ai.openclaw.sidecars.plist.template"
    ).read_text(encoding="utf-8")
    launchd_browserlab = (
        REPO_ROOT / "platform/launchd/ai.openclaw.browserlab.plist.template"
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
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    for workflow_path in workflow_dir.glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        assert "./scripts/" not in text, workflow_path.as_posix()
