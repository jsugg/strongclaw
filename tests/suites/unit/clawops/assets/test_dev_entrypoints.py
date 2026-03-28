"""Unit tests for repo-local developer entrypoints."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

from tests.utils.helpers.repo import REPO_ROOT


def _write_fake_uv(fake_bin: pathlib.Path) -> None:
    """Write one fake `uv` executable that records argv and asset-root env."""
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                "#!/usr/bin/env python3",
                "from __future__ import annotations",
                "import json",
                "import os",
                "import pathlib",
                "import sys",
                "pathlib.Path(os.environ['TEST_CAPTURE_PATH']).write_text(",
                "    json.dumps({'argv': sys.argv[1:], 'asset_root': os.environ.get('STRONGCLAW_ASSET_ROOT')}),",
                "    encoding='utf-8',",
                ")",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)


def test_clawops_dev_wrapper_exports_repo_asset_root_and_forwards_args(
    tmp_path: pathlib.Path,
) -> None:
    fake_bin = tmp_path / "fake-bin"
    capture_path = tmp_path / "capture.json"
    _write_fake_uv(fake_bin)
    wrapper_path = REPO_ROOT / "bin" / "clawops-dev"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["TEST_CAPTURE_PATH"] = str(capture_path)

    subprocess.run(
        [str(wrapper_path), "doctor", "--json"],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    payload = json.loads(capture_path.read_text(encoding="utf-8"))
    assert payload == {
        "argv": ["run", "--project", str(REPO_ROOT), "clawops", "doctor", "--json"],
        "asset_root": str(REPO_ROOT),
    }


def test_clawops_dev_wrapper_respects_preconfigured_asset_root(tmp_path: pathlib.Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    capture_path = tmp_path / "capture.json"
    _write_fake_uv(fake_bin)
    wrapper_path = REPO_ROOT / "bin" / "clawops-dev"
    configured_asset_root = tmp_path / "configured-assets"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["STRONGCLAW_ASSET_ROOT"] = str(configured_asset_root)
    env["TEST_CAPTURE_PATH"] = str(capture_path)

    subprocess.run(
        [str(wrapper_path), "config"],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    payload = json.loads(capture_path.read_text(encoding="utf-8"))
    assert payload["argv"] == ["run", "--project", str(REPO_ROOT), "clawops", "config"]
    assert payload["asset_root"] == str(configured_asset_root)
