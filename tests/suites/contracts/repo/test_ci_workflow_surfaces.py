"""Contract checks for CI workflow script surfaces."""

from __future__ import annotations

from tests.fixtures.repo import REPO_ROOT


def test_fresh_host_workflow_uses_semantic_test_scripts() -> None:
    """Fresh-host workflow should delegate to the dedicated test scripts."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "fresh-host-acceptance.yml"
    text = workflow_path.read_text(encoding="utf-8")

    assert "./tests/scripts/fresh_host.py prepare-context" in text
    assert "./tests/scripts/fresh_host.py run-scenario" in text
    assert "./tests/scripts/fresh_host.py collect-diagnostics" in text
    assert "./tests/scripts/fresh_host.py cleanup" in text
    assert "./tests/scripts/fresh_host.py write-summary" in text
    assert "./tests/scripts/hosted_docker.py install-runtime" in text
    assert "./tests/scripts/hosted_docker.py ensure-images" in text
    assert "./tests/scripts/hosted_docker.py collect-diagnostics" in text


def test_fresh_host_workflow_has_no_inline_python_or_multiline_shell_blobs() -> None:
    """Fresh-host workflow should stay thin and avoid embedded programs."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / "fresh-host-acceptance.yml"
    text = workflow_path.read_text(encoding="utf-8")

    assert "python - <<'PY'" not in text
    assert "python3 - <<'PY'" not in text
    assert "run: |" not in text
    assert ".github/scripts/fresh_host_images.py" not in text
