"""Contract checks for the devflow CI workflow."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def test_devflow_ci_workflow_exists_and_uses_uv_without_shell_blobs() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "devflow-contract.yml").read_text(
        encoding="utf-8"
    )

    assert "permissions:\n  contents: read" in workflow
    assert "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5" in workflow
    assert "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0" in workflow
    assert 'python-version: "3.12"' in workflow
    assert "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3" in workflow
    assert "uv sync --locked" in workflow
    assert "uv run python -m compileall -q src tests" in workflow
    assert "uv run --locked pytest -q -m devflow" in workflow
    assert 'uv run clawops devflow plan --project-root . --goal "contract smoke"' in workflow
    assert "@v4" not in workflow
    assert "@v6" not in workflow
    assert "python - <<'PY'" not in workflow
    assert "python3 - <<'PY'" not in workflow
    assert "run: |" not in workflow
