"""Contract checks for the devflow CI workflow."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def test_devflow_ci_workflow_exists_and_uses_uv_without_shell_blobs() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "devflow-contract.yml").read_text(
        encoding="utf-8"
    )

    assert "uv sync --locked" in workflow
    assert "uv run python -m compileall -q src tests" in workflow
    assert 'uv run clawops devflow plan --repo-root . --goal "contract smoke"' in workflow
    assert "python - <<'PY'" not in workflow
    assert "python3 - <<'PY'" not in workflow
    assert "run: |" not in workflow
