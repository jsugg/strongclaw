"""Repository contracts for the StrongClaw dev-isolation workflow."""

from __future__ import annotations

import os

from tests.utils.helpers.repo import REPO_ROOT


def test_dev_isolation_entrypoints_exist_and_are_wired() -> None:
    dev_env = REPO_ROOT / "scripts" / "dev-env.sh"
    wrapper = REPO_ROOT / "bin" / "clawops-dev"
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert dev_env.is_file()
    assert wrapper.is_file()
    assert os.access(wrapper, os.X_OK)
    assert "dev-shell: install" in makefile
    assert "./bin/clawops-dev render-openclaw-config" in makefile


def test_runtime_cli_modules_do_not_hardcode_real_home_as_the_only_default() -> None:
    openclaw_config = (REPO_ROOT / "src" / "clawops" / "openclaw_config.py").read_text(
        encoding="utf-8"
    )
    config_cli = (REPO_ROOT / "src" / "clawops" / "config_cli.py").read_text(encoding="utf-8")

    assert "Defaults to ~/.openclaw/openclaw.json." not in openclaw_config
    assert "Defaults to ~/.openclaw/openclaw.json." not in config_cli
    assert 'pathlib.Path.home() / ".openclaw" / "openclaw.json"' not in openclaw_config


def test_readme_documents_all_three_runtime_modes_and_same_user_caveat() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "bare `clawops ...`" in readme
    assert "`clawops --asset-root <repo> ...`" in readme
    assert "`source scripts/dev-env.sh`, `make dev-shell`, or `clawops-dev ...`" in readme
    assert "practical same-user developer isolation" in readme
