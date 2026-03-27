"""Contract checks for the devflow CI workflow."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def test_devflow_ci_workflow_exists_and_uses_uv_without_shell_blobs() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "devflow-contract.yml").read_text(
        encoding="utf-8"
    )

    assert "permissions:\n  contents: read" in workflow
    assert "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd # v5" in workflow
    assert "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6" in workflow
    assert 'python-version: "3.12"' in workflow
    assert "astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78 # v7.6.0" in workflow
    assert "uv sync --locked" in workflow
    assert "uv run python -m compileall -q src tests" in workflow
    assert "uv run --locked pytest -q -m devflow" in workflow
    assert 'uv run clawops devflow plan --repo-root . --goal "contract smoke"' in workflow
    assert "@v4" not in workflow
    assert "@v6" not in workflow
    assert "python - <<'PY'" not in workflow
    assert "python3 - <<'PY'" not in workflow
    assert "run: |" not in workflow
