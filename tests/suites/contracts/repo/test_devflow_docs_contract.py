"""Contract checks for the public devflow documentation."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def test_devflow_docs_surface_public_cli_and_recovery_flow() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    devflow = (REPO_ROOT / "platform/docs/DEVFLOW.md").read_text(encoding="utf-8")

    assert "clawops devflow plan" in readme
    assert "clawops devflow run" in quickstart
    assert "clawops devflow status --stuck-only" in usage
    assert "clawops devflow audit" in devflow
    assert "resume" in devflow
    assert "audit bundle" in devflow.lower()
