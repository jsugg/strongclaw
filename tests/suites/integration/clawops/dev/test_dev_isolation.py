"""Integration coverage for the repo-local dev-isolation workflow."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

from tests.utils.helpers.repo import REPO_ROOT


def _run_shell(command: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one bash login shell inside the repo root."""
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_dev_env_exports_the_default_isolated_contract() -> None:
    env = dict(os.environ)
    env.pop("STRONGCLAW_RUNTIME_ROOT", None)
    env.pop("OPENCLAW_HOME", None)
    env.pop("OPENCLAW_STATE_DIR", None)
    env.pop("OPENCLAW_CONFIG_PATH", None)
    env.pop("OPENCLAW_CONFIG", None)
    env.pop("OPENCLAW_PROFILE", None)
    result = _run_shell(
        """
        source scripts/dev-env.sh
        python -c 'import json, os; keys = (
            "STRONGCLAW_ASSET_ROOT",
            "STRONGCLAW_RUNTIME_ROOT",
            "OPENCLAW_HOME",
            "OPENCLAW_STATE_DIR",
            "OPENCLAW_CONFIG_PATH",
            "OPENCLAW_CONFIG",
            "OPENCLAW_PROFILE",
        ); print(json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True))'
        """,
        env=env,
    )
    payload = json.loads(result.stdout.strip())

    assert payload == {
        "OPENCLAW_CONFIG": str(
            REPO_ROOT / ".local" / "dev-runtime" / ".openclaw" / "openclaw.json"
        ),
        "OPENCLAW_CONFIG_PATH": str(
            REPO_ROOT / ".local" / "dev-runtime" / ".openclaw" / "openclaw.json"
        ),
        "OPENCLAW_HOME": str(REPO_ROOT / ".local" / "dev-runtime"),
        "OPENCLAW_PROFILE": "strongclaw-dev",
        "OPENCLAW_STATE_DIR": str(REPO_ROOT / ".local" / "dev-runtime" / ".openclaw"),
        "STRONGCLAW_ASSET_ROOT": str(REPO_ROOT),
        "STRONGCLAW_RUNTIME_ROOT": str(REPO_ROOT / ".local" / "dev-runtime"),
    }


def test_clawops_dev_render_openclaw_config_uses_isolated_runtime_root(
    tmp_path: pathlib.Path,
) -> None:
    runtime_root = tmp_path / "dev-runtime"
    env = dict(os.environ)
    env["STRONGCLAW_RUNTIME_ROOT"] = str(runtime_root)

    _run_shell(
        "source scripts/dev-env.sh && clawops-dev render-openclaw-config --profile hypermemory",
        env=env,
    )

    rendered_path = runtime_root / ".openclaw" / "openclaw.json"
    memory_config_dir = runtime_root / "strongclaw" / "config" / "memory"
    payload = json.loads(rendered_path.read_text(encoding="utf-8"))

    assert rendered_path.is_file()
    assert payload["gateway"]["port"] == 19001
    assert payload["agents"]["defaults"]["workspace"] == str(
        runtime_root / "strongclaw" / "data" / "workspace" / "admin"
    )
    assert (memory_config_dir / "hypermemory.yaml").is_file()
    assert (memory_config_dir / "hypermemory.sqlite.yaml").is_file()
